"""End-to-end integration tests — full invoke → parse → result cycle via stub."""

from __future__ import annotations

from pathlib import Path

import pytest

from foundation.claude.cli import invoke
from foundation.claude.events import ResultEvent, SystemInitEvent
from foundation.config import Config
from foundation.db.connection import connect
from foundation.db.schema import apply_schema
from foundation.db.tasks import create_task, get_task, update_task_status
from foundation.db.usage import get_usage_summary, record_usage


@pytest.fixture(autouse=True)
def _set_fixture_dir(monkeypatch: pytest.MonkeyPatch, fixture_dir: Path) -> None:
    monkeypatch.setenv("CLAUDE_STUB_FIXTURE_DIR", str(fixture_dir))


class TestFullCycle:
    """Test the full task lifecycle: create → invoke → record usage → complete."""

    async def test_success_lifecycle(self, test_config: Config, tmp_path: Path) -> None:
        db_path = tmp_path / "data" / "foundation.db"
        db = await connect(db_path)
        try:
            await apply_schema(db)

            # Create task
            task = await create_task(db, "Say hello")
            assert task.status == "pending"

            # Update to executing
            await update_task_status(db, task.id, "executing")

            # Invoke via stub
            result = await invoke(
                "Say hello",
                cli_command=test_config.claude.cli_command,
                model=test_config.claude.model,
                permission_mode="plan",
            )

            assert result.status == "success"
            assert result.session_id == "stub-session-001"

            # Record usage
            await record_usage(
                db,
                task_id=task.id,
                session_id=result.session_id,
                category="coding",
                input_tokens=result.usage.input_tokens,
                output_tokens=result.usage.output_tokens,
                duration_ms=result.duration_ms,
            )

            # Complete task
            await update_task_status(
                db,
                task.id,
                "complete",
                session_id=result.session_id,
                result_text=result.result_text,
            )

            # Verify final state
            final = await get_task(db, task.id)
            assert final is not None
            assert final.status == "complete"
            assert final.session_id == result.session_id
            assert final.completed_at is not None

            # Verify usage
            summary = await get_usage_summary(db)
            assert summary.record_count == 1
            assert summary.total_input_tokens > 0
        finally:
            await db.close()

    async def test_error_lifecycle(
        self, test_config: Config, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("CLAUDE_STUB_SCENARIO", "error")

        db_path = tmp_path / "data" / "foundation.db"
        db = await connect(db_path)
        try:
            await apply_schema(db)

            task = await create_task(db, "Will fail")
            await update_task_status(db, task.id, "executing")

            result = await invoke(
                "Will fail",
                cli_command=test_config.claude.cli_command,
            )

            assert result.status == "error"

            # Use the actual result text from the CLI instead of a hardcoded string
            await update_task_status(
                db,
                task.id,
                "failed",
                error_message=result.result_text,
            )

            final = await get_task(db, task.id)
            assert final is not None
            assert final.status == "failed"
            assert final.error_message == result.result_text
        finally:
            await db.close()

    async def test_events_are_captured(
        self, test_config: Config, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("CLAUDE_STUB_SCENARIO", "tools")

        result = await invoke(
            "Read a file",
            cli_command=test_config.claude.cli_command,
        )

        assert len(result.events) == 5
        assert isinstance(result.events[0], SystemInitEvent)
        assert isinstance(result.events[-1], ResultEvent)

    async def test_usage_tracking_separates_categories(self, tmp_path: Path) -> None:
        db_path = tmp_path / "data" / "foundation.db"
        db = await connect(db_path)
        try:
            await apply_schema(db)

            await record_usage(db, category="coding", input_tokens=1000, output_tokens=500)
            await record_usage(db, category="orchestrator", input_tokens=100, output_tokens=50)
            await record_usage(db, category="coding", input_tokens=2000, output_tokens=1000)

            coding = await get_usage_summary(db, category="coding")
            assert coding.total_input_tokens == 3000
            assert coding.record_count == 2

            orch = await get_usage_summary(db, category="orchestrator")
            assert orch.total_input_tokens == 100
            assert orch.record_count == 1

            total = await get_usage_summary(db)
            assert total.record_count == 3
        finally:
            await db.close()
