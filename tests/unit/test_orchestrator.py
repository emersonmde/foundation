"""Unit tests for the Orchestrator — message routing, recovery, bridge handlers."""

from __future__ import annotations

import asyncio
import contextlib

import aiosqlite
import pytest

from foundation.config import Config
from foundation.db.tasks import create_task, get_task, update_task_status
from foundation.messaging.adapter import IncomingMessage
from foundation.orchestrator import Orchestrator
from foundation.testing.telegram_stub import StubAdapter


def _make_orchestrator(
    db: aiosqlite.Connection,
    stub: StubAdapter,
    queue: asyncio.Queue[IncomingMessage],
    test_config: Config,
) -> Orchestrator:
    """Create an orchestrator wired to test doubles."""
    return Orchestrator(
        config=test_config,
        db=db,
        messaging=stub,
        incoming_queue=queue,
    )


class TestMessageRouting:
    """Test that incoming messages are stored as pending messages."""

    async def test_message_stored_as_pending(
        self, db: aiosqlite.Connection, test_config: Config
    ) -> None:
        """A message on the queue should be stored in pending_messages."""
        stub = StubAdapter()
        queue: asyncio.Queue[IncomingMessage] = asyncio.Queue()
        orch = _make_orchestrator(db, stub, queue, test_config)

        # Put a message on the queue
        queue.put_nowait(IncomingMessage.from_text("Build a widget"))

        # Run the listener briefly
        listener = asyncio.create_task(orch._message_listener())
        await asyncio.sleep(0.1)
        await orch.shutdown()
        listener.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await listener

        # Verify message was stored in pending_messages
        async with db.execute("SELECT text FROM pending_messages WHERE processed = 0") as cursor:
            rows = await cursor.fetchall()
            assert len(rows) == 1
            assert dict(rows[0])["text"] == "Build a widget"

    async def test_message_wakes_wait(self, db: aiosqlite.Connection, test_config: Config) -> None:
        """Incoming message should set the wake event."""
        stub = StubAdapter()
        queue: asyncio.Queue[IncomingMessage] = asyncio.Queue()
        orch = _make_orchestrator(db, stub, queue, test_config)

        assert not orch._wake_event.is_set()

        queue.put_nowait(IncomingMessage.from_text("Wake up"))

        listener = asyncio.create_task(orch._message_listener())
        await asyncio.sleep(0.1)
        await orch.shutdown()
        listener.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await listener

        assert orch._wake_event.is_set()


class TestRecovery:
    """Test startup recovery of non-terminal tasks."""

    async def test_recover_planning_to_pending(
        self, db: aiosqlite.Connection, test_config: Config
    ) -> None:
        """Tasks in 'planning' should be reset to 'pending'."""
        stub = StubAdapter()
        queue: asyncio.Queue[IncomingMessage] = asyncio.Queue()
        orch = _make_orchestrator(db, stub, queue, test_config)

        task = await create_task(db, "test task", "description")
        await update_task_status(db, task.id, "planning")

        await orch._recover_tasks()

        recovered = await get_task(db, task.id)
        assert recovered is not None
        assert recovered.status == "pending"

    async def test_recover_awaiting_approval_to_pending(
        self, db: aiosqlite.Connection, test_config: Config
    ) -> None:
        """Tasks in 'awaiting_approval' should be reset to 'pending'."""
        stub = StubAdapter()
        queue: asyncio.Queue[IncomingMessage] = asyncio.Queue()
        orch = _make_orchestrator(db, stub, queue, test_config)

        task = await create_task(db, "test task", "description")
        await update_task_status(db, task.id, "awaiting_approval", plan_text="the plan")

        await orch._recover_tasks()

        recovered = await get_task(db, task.id)
        assert recovered is not None
        assert recovered.status == "pending"
        # plan_text should still be in DB
        assert recovered.plan_text == "the plan"

    async def test_recover_executing_to_failed(
        self, db: aiosqlite.Connection, test_config: Config
    ) -> None:
        """Tasks in 'executing' should be marked 'failed'."""
        stub = StubAdapter()
        queue: asyncio.Queue[IncomingMessage] = asyncio.Queue()
        orch = _make_orchestrator(db, stub, queue, test_config)

        task = await create_task(db, "test task", "description")
        await update_task_status(db, task.id, "executing")

        await orch._recover_tasks()

        recovered = await get_task(db, task.id)
        assert recovered is not None
        assert recovered.status == "failed"
        assert recovered.error_message is not None
        assert "interrupted" in recovered.error_message.lower()

        # Should notify the user
        assert len(stub.sent_messages) == 1
        assert "interrupted" in stub.sent_messages[0].text.lower()

    async def test_recover_ignores_terminal_tasks(
        self, db: aiosqlite.Connection, test_config: Config
    ) -> None:
        """Terminal tasks (complete, failed, cancelled) should not be changed."""
        stub = StubAdapter()
        queue: asyncio.Queue[IncomingMessage] = asyncio.Queue()
        orch = _make_orchestrator(db, stub, queue, test_config)

        task_complete = await create_task(db, "done task", "d")
        await update_task_status(db, task_complete.id, "complete")

        task_failed = await create_task(db, "failed task", "d")
        await update_task_status(db, task_failed.id, "failed")

        await orch._recover_tasks()

        recovered_complete = await get_task(db, task_complete.id)
        assert recovered_complete is not None
        assert recovered_complete.status == "complete"

        recovered_failed = await get_task(db, task_failed.id)
        assert recovered_failed is not None
        assert recovered_failed.status == "failed"
        assert len(stub.sent_messages) == 0

    async def test_recover_marks_running_agents_failed(
        self, db: aiosqlite.Connection, test_config: Config
    ) -> None:
        """Running agent sessions should be marked failed on restart."""
        stub = StubAdapter()
        queue: asyncio.Queue[IncomingMessage] = asyncio.Queue()
        orch = _make_orchestrator(db, stub, queue, test_config)

        task = await create_task(db, "agent task", "d")
        await db.execute(
            "INSERT INTO agent_sessions (task_id, mode, status, started_at) VALUES (?, ?, ?, ?)",
            (task.id, "plan", "running", "2026-02-25T12:00:00+00:00"),
        )
        await db.commit()

        await orch._recover_tasks()

        async with db.execute(
            "SELECT status FROM agent_sessions WHERE task_id = ?", (task.id,)
        ) as cursor:
            row = await cursor.fetchone()
            assert row is not None
            assert dict(row)["status"] == "failed"


class TestBridgeHandlers:
    """Test the IPC bridge handler methods."""

    async def test_bridge_send_message(
        self, db: aiosqlite.Connection, test_config: Config
    ) -> None:
        stub = StubAdapter()
        queue: asyncio.Queue[IncomingMessage] = asyncio.Queue()
        orch = _make_orchestrator(db, stub, queue, test_config)

        result = await orch._bridge_send_message({"text": "Hello human"})
        assert "message_id" in result
        assert len(stub.sent_messages) == 1
        assert stub.sent_messages[0].text == "Hello human"

    async def test_bridge_send_message_with_buttons(
        self, db: aiosqlite.Connection, test_config: Config
    ) -> None:
        stub = StubAdapter()
        queue: asyncio.Queue[IncomingMessage] = asyncio.Queue()
        orch = _make_orchestrator(db, stub, queue, test_config)

        result = await orch._bridge_send_message(
            {
                "text": "Choose one",
                "buttons": [{"text": "A", "data": "a"}, {"text": "B", "data": "b"}],
            }
        )
        assert "message_id" in result
        assert stub.sent_messages[0].buttons is not None

    async def test_bridge_wait_early_wake(
        self, db: aiosqlite.Connection, test_config: Config
    ) -> None:
        stub = StubAdapter()
        queue: asyncio.Queue[IncomingMessage] = asyncio.Queue()
        orch = _make_orchestrator(db, stub, queue, test_config)

        # Set the wake event after a short delay (simulates incoming message during wait)
        async def wake_soon() -> None:
            await asyncio.sleep(0.1)
            orch._wake_event.set()

        wake_task = asyncio.create_task(wake_soon())
        result = await orch._bridge_wait({"seconds": 30})
        await wake_task
        assert result["reason"] == "message_received"

    async def test_bridge_spawn_cancel_agent(
        self, db: aiosqlite.Connection, test_config: Config
    ) -> None:
        stub = StubAdapter()
        queue: asyncio.Queue[IncomingMessage] = asyncio.Queue()
        orch = _make_orchestrator(db, stub, queue, test_config)

        task = await create_task(db, "spawn test", "d")

        # Spawn
        result = await orch._bridge_spawn_agent(
            {
                "task_id": task.id,
                "prompt": "Plan something",
                "mode": "plan",
            }
        )
        assert result.get("status") == "spawned"

        # Verify agent session recorded in DB
        async with db.execute(
            "SELECT status FROM agent_sessions WHERE task_id = ?", (task.id,)
        ) as cursor:
            row = await cursor.fetchone()
            assert row is not None
            assert dict(row)["status"] == "running"

        # Cancel
        cancel_result = await orch._bridge_cancel_agent({"task_id": task.id})
        assert cancel_result["cancelled"] is True

    async def test_bridge_query_agents(
        self, db: aiosqlite.Connection, test_config: Config
    ) -> None:
        stub = StubAdapter()
        queue: asyncio.Queue[IncomingMessage] = asyncio.Queue()
        orch = _make_orchestrator(db, stub, queue, test_config)

        result = await orch._bridge_query_agents({})
        assert result["agents"] == []


class TestMcpConfigGeneration:
    """Test MCP config file generation."""

    async def test_write_mcp_config(self, db: aiosqlite.Connection, test_config: Config) -> None:
        import json

        stub = StubAdapter()
        queue: asyncio.Queue[IncomingMessage] = asyncio.Queue()
        orch = _make_orchestrator(db, stub, queue, test_config)

        path = orch._write_mcp_config()
        assert path.exists()
        config = json.loads(path.read_text())
        assert "mcpServers" in config
        assert "foundation" in config["mcpServers"]
        server = config["mcpServers"]["foundation"]
        assert server["command"] == "python"
        assert "foundation.mcp.server" in server["args"]
        assert "FOUNDATION_DB_PATH" in server["env"]
        assert "FOUNDATION_IPC_SOCKET" in server["env"]
        path.unlink()


class TestPermissionDenialForwarding:
    """Test deterministic forwarding of permission denials."""

    async def test_forward_denials(self, db: aiosqlite.Connection, test_config: Config) -> None:
        from foundation.claude.cli import ClaudeResult
        from foundation.claude.events import PermissionDenial, TokenUsage

        stub = StubAdapter()
        queue: asyncio.Queue[IncomingMessage] = asyncio.Queue()
        orch = _make_orchestrator(db, stub, queue, test_config)

        result = ClaudeResult(
            session_id="test",
            result_text="denied",
            status="success",
            duration_ms=100,
            events=[],
            usage=TokenUsage(),
            return_code=0,
            permission_denials=[
                PermissionDenial(
                    tool_name="Edit",
                    tool_use_id="toolu_1",
                    tool_input={"file_path": "/x"},
                ),
                PermissionDenial(
                    tool_name="Bash",
                    tool_use_id="toolu_2",
                    tool_input={"command": "rm -rf /"},
                ),
            ],
        )

        await orch._forward_permission_denials(result)

        assert len(stub.sent_messages) == 2
        assert "Edit" in stub.sent_messages[0].text
        assert "Bash" in stub.sent_messages[1].text


class TestIncomingMessage:
    """Test IncomingMessage dataclass."""

    def test_from_text(self) -> None:
        msg = IncomingMessage.from_text("hello")
        assert msg.text == "hello"
        assert msg.timestamp > 0

    def test_frozen(self) -> None:
        msg = IncomingMessage.from_text("hello")
        with pytest.raises(AttributeError):
            setattr(msg, "text", "bye")  # noqa: B010
