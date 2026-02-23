"""Unit tests for the Orchestrator — state transitions, message routing, recovery."""

from __future__ import annotations

import asyncio
import contextlib

import aiosqlite
import pytest

from foundation.config import ClaudeConfig
from foundation.db.tasks import create_task, get_task, list_tasks, update_task_status
from foundation.messaging.adapter import IncomingMessage
from foundation.orchestrator import Orchestrator
from foundation.testing.telegram_stub import StubAdapter


def _make_orchestrator(
    db: aiosqlite.Connection,
    stub: StubAdapter,
    queue: asyncio.Queue[IncomingMessage],
    stub_command: str,
) -> Orchestrator:
    """Create an orchestrator wired to test doubles."""
    claude_config = ClaudeConfig(cli_command=stub_command)
    return Orchestrator(
        claude_config=claude_config,
        db=db,
        messaging=stub,
        incoming_queue=queue,
    )


class TestMessageRouting:
    """Test that incoming messages create tasks."""

    async def test_message_creates_task(self, db: aiosqlite.Connection) -> None:
        """A message on the queue should create a DB task."""
        stub = StubAdapter()
        queue: asyncio.Queue[IncomingMessage] = asyncio.Queue()
        orch = _make_orchestrator(db, stub, queue, "echo")

        # Put a message on the queue
        queue.put_nowait(IncomingMessage.from_text("Build a widget"))

        # Run the listener briefly
        listener = asyncio.create_task(orch._message_listener())
        await asyncio.sleep(0.1)
        await orch.shutdown()
        listener.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await listener

        # Verify task was created
        tasks = await list_tasks(db)
        assert len(tasks) == 1
        assert tasks[0].title == "Build a widget"
        assert tasks[0].status == "pending"

        # Verify notification was sent
        assert len(stub.sent_messages) == 1
        assert "Task created" in stub.sent_messages[0].text

    async def test_long_message_truncates_title(self, db: aiosqlite.Connection) -> None:
        """Title should be truncated to 100 chars."""
        stub = StubAdapter()
        queue: asyncio.Queue[IncomingMessage] = asyncio.Queue()
        orch = _make_orchestrator(db, stub, queue, "echo")

        long_text = "x" * 200
        queue.put_nowait(IncomingMessage.from_text(long_text))

        listener = asyncio.create_task(orch._message_listener())
        await asyncio.sleep(0.1)
        await orch.shutdown()
        listener.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await listener

        tasks = await list_tasks(db)
        assert len(tasks) == 1
        assert len(tasks[0].title) == 100
        assert tasks[0].description == long_text


class TestRecovery:
    """Test startup recovery of non-terminal tasks."""

    async def test_recover_planning_to_pending(self, db: aiosqlite.Connection) -> None:
        """Tasks in 'planning' should be reset to 'pending'."""
        stub = StubAdapter()
        queue: asyncio.Queue[IncomingMessage] = asyncio.Queue()
        orch = _make_orchestrator(db, stub, queue, "echo")

        task = await create_task(db, "test task", "description")
        await update_task_status(db, task.id, "planning")

        await orch._recover_tasks()

        recovered = await get_task(db, task.id)
        assert recovered is not None
        assert recovered.status == "pending"

    async def test_recover_awaiting_approval_to_pending(self, db: aiosqlite.Connection) -> None:
        """Tasks in 'awaiting_approval' should be reset to 'pending'."""
        stub = StubAdapter()
        queue: asyncio.Queue[IncomingMessage] = asyncio.Queue()
        orch = _make_orchestrator(db, stub, queue, "echo")

        task = await create_task(db, "test task", "description")
        await update_task_status(db, task.id, "awaiting_approval", plan_text="the plan")

        await orch._recover_tasks()

        recovered = await get_task(db, task.id)
        assert recovered is not None
        assert recovered.status == "pending"
        # plan_text should still be in DB
        assert recovered.plan_text == "the plan"

    async def test_recover_executing_to_failed(self, db: aiosqlite.Connection) -> None:
        """Tasks in 'executing' should be marked 'failed'."""
        stub = StubAdapter()
        queue: asyncio.Queue[IncomingMessage] = asyncio.Queue()
        orch = _make_orchestrator(db, stub, queue, "echo")

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

    async def test_recover_ignores_terminal_tasks(self, db: aiosqlite.Connection) -> None:
        """Terminal tasks (complete, failed, cancelled) should not be changed."""
        stub = StubAdapter()
        queue: asyncio.Queue[IncomingMessage] = asyncio.Queue()
        orch = _make_orchestrator(db, stub, queue, "echo")

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

    async def test_recover_sets_work_available(self, db: aiosqlite.Connection) -> None:
        """Recovery of tasks should set the work_available event."""
        stub = StubAdapter()
        queue: asyncio.Queue[IncomingMessage] = asyncio.Queue()
        orch = _make_orchestrator(db, stub, queue, "echo")

        task = await create_task(db, "test", "d")
        await update_task_status(db, task.id, "planning")

        assert not orch._work_available.is_set()
        await orch._recover_tasks()
        assert orch._work_available.is_set()


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
