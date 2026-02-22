"""Tests for SQLite schema and CRUD operations."""

from __future__ import annotations

import aiosqlite
import pytest

from foundation.db.schema import CURRENT_VERSION, apply_schema, get_schema_version
from foundation.db.tasks import create_task, get_task, list_tasks, update_task_status
from foundation.db.usage import get_usage_summary, record_usage


class TestSchema:
    async def test_apply_schema_creates_tables(self, db: aiosqlite.Connection) -> None:
        # Verify tables exist by querying them
        async with db.execute("SELECT count(*) FROM tasks") as cursor:
            row = await cursor.fetchone()
            assert row is not None
            assert row[0] == 0

        async with db.execute("SELECT count(*) FROM usage_records") as cursor:
            row = await cursor.fetchone()
            assert row is not None
            assert row[0] == 0

    async def test_schema_version(self, db: aiosqlite.Connection) -> None:
        version = await get_schema_version(db)
        assert version == CURRENT_VERSION

    async def test_apply_schema_idempotent(self, db: aiosqlite.Connection) -> None:
        # Applying schema twice should not error
        await apply_schema(db)
        version = await get_schema_version(db)
        assert version == CURRENT_VERSION


class TestTasks:
    async def test_create_and_get(self, db: aiosqlite.Connection) -> None:
        task = await create_task(db, "Test task", "Description")
        assert task.title == "Test task"
        assert task.status == "pending"

        fetched = await get_task(db, task.id)
        assert fetched is not None
        assert fetched.title == "Test task"

    async def test_get_nonexistent(self, db: aiosqlite.Connection) -> None:
        result = await get_task(db, "nonexistent-id")
        assert result is None

    async def test_update_status(self, db: aiosqlite.Connection) -> None:
        task = await create_task(db, "Task")
        await update_task_status(db, task.id, "planning", session_id="sess-1")

        updated = await get_task(db, task.id)
        assert updated is not None
        assert updated.status == "planning"
        assert updated.session_id == "sess-1"

    async def test_complete_sets_completed_at(self, db: aiosqlite.Connection) -> None:
        task = await create_task(db, "Task")
        await update_task_status(db, task.id, "complete", result_text="Done!")

        updated = await get_task(db, task.id)
        assert updated is not None
        assert updated.completed_at is not None
        assert updated.result_text == "Done!"

    async def test_failed_sets_completed_at(self, db: aiosqlite.Connection) -> None:
        task = await create_task(db, "Task")
        await update_task_status(db, task.id, "failed", error_message="Boom")

        updated = await get_task(db, task.id)
        assert updated is not None
        assert updated.completed_at is not None
        assert updated.error_message == "Boom"

    async def test_list_tasks(self, db: aiosqlite.Connection) -> None:
        await create_task(db, "Task 1")
        await create_task(db, "Task 2")

        tasks = await list_tasks(db)
        assert len(tasks) == 2

    async def test_list_tasks_by_status(self, db: aiosqlite.Connection) -> None:
        t1 = await create_task(db, "Task 1")
        await create_task(db, "Task 2")
        await update_task_status(db, t1.id, "complete")

        pending = await list_tasks(db, status="pending")
        assert len(pending) == 1
        assert pending[0].title == "Task 2"

    async def test_invalid_status_rejected(self, db: aiosqlite.Connection) -> None:
        task = await create_task(db, "Task")
        with pytest.raises(aiosqlite.IntegrityError):
            await update_task_status(db, task.id, "bogus_status")
        # Verify the task status was not changed
        unchanged = await get_task(db, task.id)
        assert unchanged is not None
        assert unchanged.status == "pending"


class TestUsage:
    async def test_record_and_summarize(self, db: aiosqlite.Connection) -> None:
        row_id = await record_usage(
            db,
            session_id="sess-1",
            category="coding",
            model="sonnet",
            input_tokens=100,
            output_tokens=50,
            duration_ms=1000,
        )
        assert row_id > 0

        summary = await get_usage_summary(db)
        assert summary.total_input_tokens == 100
        assert summary.total_output_tokens == 50
        assert summary.record_count == 1

    async def test_filter_by_category(self, db: aiosqlite.Connection) -> None:
        await record_usage(db, category="coding", input_tokens=100)
        await record_usage(db, category="orchestrator", input_tokens=50)

        coding = await get_usage_summary(db, category="coding")
        assert coding.total_input_tokens == 100
        assert coding.record_count == 1

        orch = await get_usage_summary(db, category="orchestrator")
        assert orch.total_input_tokens == 50

    async def test_empty_summary(self, db: aiosqlite.Connection) -> None:
        summary = await get_usage_summary(db)
        assert summary.total_input_tokens == 0
        assert summary.record_count == 0

    async def test_usage_with_task_id(self, db: aiosqlite.Connection) -> None:
        task = await create_task(db, "Task")
        row_id = await record_usage(
            db,
            task_id=task.id,
            input_tokens=200,
            output_tokens=100,
        )
        assert row_id > 0
