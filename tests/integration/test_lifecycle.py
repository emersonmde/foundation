"""Integration tests for the MCP server, IPC bridge, and state snapshot."""

from __future__ import annotations

import asyncio
from pathlib import Path

import aiosqlite
import pytest

from foundation.agents.registry import AgentRegistry
from foundation.config import Config
from foundation.db.tasks import create_task, update_task_status
from foundation.mcp.bridge import BridgeClient, BridgeServer
from foundation.mcp.tools import (
    TOOL_DEFINITIONS,
    handle_create_task,
    handle_query_tasks,
    handle_read_memory,
    handle_update_memory,
    handle_update_task,
)
from foundation.state import build_state_snapshot


class TestMcpToolHandlers:
    """Test MCP tool handlers against a real database."""

    async def test_create_task_handler(self, db: aiosqlite.Connection) -> None:
        result = await handle_create_task(db, {"title": "New task", "description": "Details"})
        assert "task" in result
        assert result["task"]["title"] == "New task"
        assert result["task"]["status"] == "pending"

    async def test_create_task_requires_title(self, db: aiosqlite.Connection) -> None:
        result = await handle_create_task(db, {})
        assert "error" in result

    async def test_update_task_handler(self, db: aiosqlite.Connection) -> None:
        task = await create_task(db, "Test", "desc")
        result = await handle_update_task(db, {"task_id": task.id, "status": "planning"})
        assert result["task"]["status"] == "planning"

    async def test_update_task_not_found(self, db: aiosqlite.Connection) -> None:
        result = await handle_update_task(db, {"task_id": "nonexistent", "status": "planning"})
        assert "error" in result

    async def test_query_tasks_handler(self, db: aiosqlite.Connection) -> None:
        await create_task(db, "Task A", "a")
        await create_task(db, "Task B", "b")
        result = await handle_query_tasks(db, {})
        assert len(result["tasks"]) == 2

    async def test_query_tasks_with_filter(self, db: aiosqlite.Connection) -> None:
        t = await create_task(db, "Task A", "a")
        await create_task(db, "Task B", "b")
        await update_task_status(db, t.id, "complete")
        result = await handle_query_tasks(db, {"status": "pending"})
        assert len(result["tasks"]) == 1
        assert result["tasks"][0]["title"] == "Task B"

    async def test_query_tasks_with_limit(self, db: aiosqlite.Connection) -> None:
        for i in range(5):
            await create_task(db, f"Task {i}", "d")
        result = await handle_query_tasks(db, {"limit": 3})
        assert len(result["tasks"]) == 3


class TestMemoryTools:
    """Test memory read/write tools."""

    async def test_read_nonexistent(self, tmp_path: Path) -> None:
        result = await handle_read_memory(tmp_path / "memory", {"topic": "notes"})
        assert result["exists"] is False
        assert result["content"] == ""

    async def test_write_and_read(self, tmp_path: Path) -> None:
        memory_dir = tmp_path / "memory"
        await handle_update_memory(memory_dir, {"topic": "notes", "content": "# Notes\nHello"})
        result = await handle_read_memory(memory_dir, {"topic": "notes"})
        assert result["exists"] is True
        assert "Hello" in result["content"]

    async def test_topic_sanitization(self, tmp_path: Path) -> None:
        memory_dir = tmp_path / "memory"
        result = await handle_update_memory(
            memory_dir, {"topic": "../../etc/passwd", "content": "hack"}
        )
        assert result["status"] == "updated"
        # Should write to sanitized filename
        assert "etcpasswd" in result["path"]

    async def test_invalid_topic(self, tmp_path: Path) -> None:
        memory_dir = tmp_path / "memory"
        result = await handle_read_memory(memory_dir, {"topic": "..."})
        assert "error" in result


class TestToolDefinitions:
    """Test that tool definitions are valid."""

    def test_all_tools_have_required_fields(self) -> None:
        for tool in TOOL_DEFINITIONS:
            assert tool.name
            assert tool.description
            assert "type" in tool.input_schema
            assert tool.input_schema["type"] == "object"

    def test_expected_tools_present(self) -> None:
        names = {t.name for t in TOOL_DEFINITIONS}
        expected = {
            "create_task",
            "update_task",
            "query_tasks",
            "send_message",
            "request_human_input",
            "spawn_agent",
            "cancel_agent",
            "read_memory",
            "update_memory",
            "wait",
        }
        assert expected.issubset(names)

    def test_to_dict(self) -> None:
        tool = TOOL_DEFINITIONS[0]
        d = tool.to_dict()
        assert "name" in d
        assert "description" in d
        assert "inputSchema" in d


class TestIpcBridge:
    """Test the IPC bridge client/server communication."""

    async def test_roundtrip(self, tmp_path: Path) -> None:
        socket_path = tmp_path / "test-bridge.sock"
        server = BridgeServer(socket_path)

        async def echo_handler(params: dict) -> dict:  # type: ignore[type-arg]
            return {"echo": params.get("message", "")}

        server.register("echo", echo_handler)
        await server.start()

        try:
            client = BridgeClient(socket_path)
            await client.connect()

            result = await client.call("echo", {"message": "hello"})
            assert result["echo"] == "hello"

            await client.close()
        finally:
            await server.stop()

    async def test_unknown_method(self, tmp_path: Path) -> None:
        socket_path = tmp_path / "test-bridge.sock"
        server = BridgeServer(socket_path)
        await server.start()

        try:
            client = BridgeClient(socket_path)
            await client.connect()

            with pytest.raises(RuntimeError, match="unknown method"):
                await client.call("nonexistent", {})

            await client.close()
        finally:
            await server.stop()

    async def test_multiple_calls(self, tmp_path: Path) -> None:
        socket_path = tmp_path / "test-bridge.sock"
        server = BridgeServer(socket_path)

        call_count = 0

        async def counter(params: dict) -> dict:  # type: ignore[type-arg]
            nonlocal call_count
            call_count += 1
            return {"count": call_count}

        server.register("count", counter)
        await server.start()

        try:
            client = BridgeClient(socket_path)
            await client.connect()

            r1 = await client.call("count", {})
            r2 = await client.call("count", {})
            r3 = await client.call("count", {})
            assert r1["count"] == 1
            assert r2["count"] == 2
            assert r3["count"] == 3

            await client.close()
        finally:
            await server.stop()


class TestStateSnapshot:
    """Test the state snapshot builder."""

    async def test_empty_state(self, db: aiosqlite.Connection) -> None:
        registry = AgentRegistry()
        snapshot = await build_state_snapshot(db, registry)
        assert "## Current State" in snapshot
        assert "No tasks" in snapshot

    async def test_with_tasks(self, db: aiosqlite.Connection) -> None:
        await create_task(db, "Task A", "a")
        await create_task(db, "Task B", "b")
        registry = AgentRegistry()
        snapshot = await build_state_snapshot(db, registry)
        assert "Task A" in snapshot
        assert "Task B" in snapshot
        assert "[pending]" in snapshot

    async def test_with_pending_messages(self, db: aiosqlite.Connection) -> None:
        await db.execute(
            "INSERT INTO pending_messages (text, received_at) VALUES (?, ?)",
            ("Hello from human", "2026-02-25T12:00:00+00:00"),
        )
        await db.commit()
        registry = AgentRegistry()
        snapshot = await build_state_snapshot(db, registry)
        assert "Hello from human" in snapshot
        assert "Pending Messages" in snapshot

    async def test_with_action_log(self, db: aiosqlite.Connection) -> None:
        await db.execute(
            "INSERT INTO action_log (iteration_id, action_type, summary, created_at)"
            " VALUES (?, ?, ?, ?)",
            ("iter-1", "tool_call", "Spawned agent", "2026-02-25T12:00:00+00:00"),
        )
        await db.commit()
        registry = AgentRegistry()
        snapshot = await build_state_snapshot(db, registry)
        assert "Spawned agent" in snapshot
        assert "Recent Actions" in snapshot


class TestAgentRegistry:
    """Test sub-agent process tracking."""

    async def test_spawn_and_list(self, test_config: Config) -> None:
        registry = AgentRegistry()
        info = await registry.spawn(
            "task-1",
            "Do something",
            cli_command=test_config.claude.cli_command,
        )
        assert info.task_id == "task-1"
        assert info.mode == "plan"
        assert info.is_running

        active = registry.list_active()
        assert len(active) == 1

        # Cancel for cleanup
        await registry.cancel("task-1")

    async def test_cancel_nonexistent(self) -> None:
        registry = AgentRegistry()
        assert not await registry.cancel("nonexistent")

    async def test_duplicate_spawn_raises(self, test_config: Config) -> None:
        registry = AgentRegistry()
        await registry.spawn(
            "task-1",
            "First",
            cli_command=test_config.claude.cli_command,
        )
        with pytest.raises(ValueError, match="already running"):
            await registry.spawn(
                "task-1",
                "Second",
                cli_command=test_config.claude.cli_command,
            )
        await registry.cancel("task-1")

    async def test_get(self, test_config: Config) -> None:
        registry = AgentRegistry()
        assert registry.get("task-1") is None
        await registry.spawn(
            "task-1",
            "Test",
            cli_command=test_config.claude.cli_command,
        )
        info = registry.get("task-1")
        assert info is not None
        assert info.task_id == "task-1"
        await registry.cancel("task-1")

    async def test_collect_finished(self, test_config: Config) -> None:
        registry = AgentRegistry()
        await registry.spawn(
            "task-1",
            "Quick task",
            cli_command=test_config.claude.cli_command,
        )
        # Wait for the stub to complete
        await asyncio.sleep(1.0)
        finished = registry.collect_finished()
        assert len(finished) == 1
        task_id, result = finished[0]
        assert task_id == "task-1"
        assert result.status == "success"
