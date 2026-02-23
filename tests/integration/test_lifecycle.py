"""Integration tests for the full task lifecycle through the orchestrator."""

from __future__ import annotations

import asyncio
from pathlib import Path

import aiosqlite
import pytest

from foundation.config import ClaudeConfig
from foundation.db.tasks import list_tasks
from foundation.messaging.adapter import IncomingMessage
from foundation.orchestrator import Orchestrator
from foundation.testing.telegram_stub import StubAdapter


@pytest.fixture(autouse=True)
def _set_fixture_dir(monkeypatch: pytest.MonkeyPatch, fixture_dir: Path) -> None:
    monkeypatch.setenv("CLAUDE_STUB_FIXTURE_DIR", str(fixture_dir))


def _make_orchestrator(
    db: aiosqlite.Connection,
    stub: StubAdapter,
    queue: asyncio.Queue[IncomingMessage],
    stub_command: str,
) -> Orchestrator:
    return Orchestrator(
        claude_config=ClaudeConfig(cli_command=stub_command),
        db=db,
        messaging=stub,
        incoming_queue=queue,
    )


async def _wait_for_status(
    db: aiosqlite.Connection,
    target_statuses: set[str],
    *,
    max_polls: int = 100,
    interval: float = 0.05,
) -> None:
    """Poll until a task reaches one of the target statuses.

    Raises AssertionError if the status is not reached within the polling window.
    """
    for _ in range(max_polls):
        await asyncio.sleep(interval)
        tasks = await list_tasks(db)
        if tasks and tasks[0].status in target_statuses:
            return
    tasks = await list_tasks(db)
    actual = tasks[0].status if tasks else "(no tasks)"
    raise AssertionError(
        f"Task did not reach {target_statuses} within {max_polls * interval}s, "
        f"current status: {actual}"
    )


class TestFullLifecycle:
    """Test the complete: submit → plan → approve → execute → complete flow."""

    async def test_approve_flow(
        self,
        db: aiosqlite.Connection,
        stub_command: str,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Submit task → plan → approve → execute → complete."""
        monkeypatch.setenv("CLAUDE_STUB_SCENARIO", "auto")
        stub = StubAdapter()
        queue: asyncio.Queue[IncomingMessage] = asyncio.Queue()
        orch = _make_orchestrator(db, stub, queue, stub_command)

        queue.put_nowait(IncomingMessage.from_text("Build feature X"))

        async def interact() -> None:
            # Wait for the plan approval message to appear
            for _ in range(100):
                await asyncio.sleep(0.05)
                for msg in stub.sent_messages:
                    if msg.buttons is not None:
                        stub.inject_callback(msg.message_id, "approve")
                        return

        orch_task = asyncio.create_task(orch.run())
        await asyncio.wait_for(interact(), timeout=10.0)
        await _wait_for_status(db, {"complete", "failed"})

        await orch.shutdown()
        await asyncio.wait_for(orch_task, timeout=5.0)

        tasks = await list_tasks(db)
        assert len(tasks) == 1
        assert tasks[0].status == "complete"

        texts = [m.text for m in stub.sent_messages]
        assert any("Task created" in t for t in texts)
        assert any("Plan for" in t for t in texts)
        assert any("Executing" in t for t in texts)
        assert any("Task complete" in t for t in texts)

    async def test_reject_flow(
        self,
        db: aiosqlite.Connection,
        stub_command: str,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Submit task → plan → reject → cancelled."""
        monkeypatch.setenv("CLAUDE_STUB_SCENARIO", "auto")
        stub = StubAdapter()
        queue: asyncio.Queue[IncomingMessage] = asyncio.Queue()
        orch = _make_orchestrator(db, stub, queue, stub_command)

        queue.put_nowait(IncomingMessage.from_text("Bad idea"))

        async def reject_plan() -> None:
            for _ in range(100):
                await asyncio.sleep(0.05)
                for msg in stub.sent_messages:
                    if msg.buttons is not None:
                        stub.inject_callback(msg.message_id, "reject")
                        return

        orch_task = asyncio.create_task(orch.run())
        await asyncio.wait_for(reject_plan(), timeout=10.0)
        await _wait_for_status(db, {"cancelled"})

        await orch.shutdown()
        await asyncio.wait_for(orch_task, timeout=5.0)

        tasks = await list_tasks(db)
        assert len(tasks) == 1
        assert tasks[0].status == "cancelled"

        texts = [m.text for m in stub.sent_messages]
        assert any("cancelled" in t.lower() for t in texts)

    async def test_modify_flow(
        self,
        db: aiosqlite.Connection,
        stub_command: str,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Submit → plan → modify → re-plan → approve → execute → complete."""
        monkeypatch.setenv("CLAUDE_STUB_SCENARIO", "auto")
        stub = StubAdapter()
        queue: asyncio.Queue[IncomingMessage] = asyncio.Queue()
        orch = _make_orchestrator(db, stub, queue, stub_command)

        queue.put_nowait(IncomingMessage.from_text("Build feature Y"))

        modify_done = False

        async def modify_then_approve() -> None:
            nonlocal modify_done
            # First: wait for plan and click modify
            for _ in range(100):
                await asyncio.sleep(0.05)
                for msg in stub.sent_messages:
                    if msg.buttons is not None and not modify_done:
                        stub.inject_callback(msg.message_id, "modify")
                        modify_done = True
                        break
                if modify_done:
                    break

            # Wait for "Send your feedback" prompt, then inject reply
            for _ in range(100):
                await asyncio.sleep(0.05)
                texts = [m.text for m in stub.sent_messages]
                if any("feedback" in t.lower() for t in texts):
                    await asyncio.sleep(0.05)
                    stub.inject_reply("Use a different approach")
                    break

            # Wait for second plan and approve
            for _ in range(100):
                await asyncio.sleep(0.05)
                for msg in stub.sent_messages:
                    if msg.buttons is not None and not msg.edited:
                        stub.inject_callback(msg.message_id, "approve")
                        return

        orch_task = asyncio.create_task(orch.run())
        await asyncio.wait_for(modify_then_approve(), timeout=10.0)
        await _wait_for_status(db, {"complete", "failed"})

        await orch.shutdown()
        await asyncio.wait_for(orch_task, timeout=5.0)

        tasks = await list_tasks(db)
        assert len(tasks) == 1
        assert tasks[0].status == "complete"

        texts = [m.text for m in stub.sent_messages]
        assert any("feedback" in t.lower() for t in texts)
        assert any("Plan for" in t for t in texts)


class TestFailureHandling:
    """Test that failures during planning/execution are handled gracefully."""

    async def test_planning_failure(
        self,
        db: aiosqlite.Connection,
        stub_command: str,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """When planning fails, task should be marked failed."""
        monkeypatch.setenv("CLAUDE_STUB_SCENARIO", "error")
        stub = StubAdapter()
        queue: asyncio.Queue[IncomingMessage] = asyncio.Queue()
        orch = _make_orchestrator(db, stub, queue, stub_command)

        queue.put_nowait(IncomingMessage.from_text("Broken task"))

        orch_task = asyncio.create_task(orch.run())
        await _wait_for_status(db, {"failed"})

        await orch.shutdown()
        await asyncio.wait_for(orch_task, timeout=5.0)

        tasks = await list_tasks(db)
        assert len(tasks) == 1
        assert tasks[0].status == "failed"

        texts = [m.text for m in stub.sent_messages]
        assert any("failed" in t.lower() for t in texts)
