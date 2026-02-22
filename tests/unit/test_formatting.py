"""Tests for Telegram formatting utilities."""

from __future__ import annotations

from foundation.db.tasks import Task
from foundation.db.usage import UsageSummary
from foundation.telegram.formatting import (
    escape_html,
    format_help,
    format_status,
    split_message,
)


class TestEscapeHtml:
    def test_escapes_angle_brackets(self) -> None:
        assert escape_html("<b>bold</b>") == "&lt;b&gt;bold&lt;/b&gt;"

    def test_escapes_ampersand(self) -> None:
        assert escape_html("a & b") == "a &amp; b"

    def test_escapes_quotes(self) -> None:
        assert escape_html('"hello"') == "&quot;hello&quot;"

    def test_escapes_single_quotes(self) -> None:
        assert escape_html("it's") == "it&#x27;s"

    def test_passthrough_plain_text(self) -> None:
        assert escape_html("hello world") == "hello world"


class TestSplitMessage:
    def test_short_message_unchanged(self) -> None:
        assert split_message("hello") == ["hello"]

    def test_empty_string(self) -> None:
        assert split_message("") == [""]

    def test_exact_limit(self) -> None:
        text = "a" * 4096
        assert split_message(text) == [text]

    def test_splits_on_newline(self) -> None:
        line = "x" * 50
        text = "\n".join([line] * 100)  # 100 lines of 50 chars + newlines
        chunks = split_message(text, max_length=200)
        for chunk in chunks:
            assert len(chunk) <= 200

    def test_force_splits_without_newlines(self) -> None:
        text = "a" * 300
        chunks = split_message(text, max_length=100)
        assert len(chunks) == 3
        assert "".join(chunks) == text

    def test_preserves_all_content(self) -> None:
        lines = [f"line {i}" for i in range(50)]
        text = "\n".join(lines)
        chunks = split_message(text, max_length=100)
        # Every original line must appear in exactly one chunk
        all_lines_in_chunks = set()
        for chunk in chunks:
            for line in chunk.split("\n"):
                if line:
                    all_lines_in_chunks.add(line)
        assert all_lines_in_chunks >= set(lines)

    def test_leading_newline_splits_correctly(self) -> None:
        text = "\n" + "a" * 200
        chunks = split_message(text, max_length=100)
        reconstructed = "\n".join(chunks)
        assert "a" * 200 in reconstructed.replace("\n", "")


class TestFormatStatus:
    def _make_task(self, title: str, status: str = "pending") -> Task:
        return Task(
            id="test-id",
            title=title,
            description="",
            status=status,
            session_id=None,
            plan_text=None,
            result_text=None,
            error_message=None,
            created_at="2026-01-01",
            updated_at="2026-01-01",
            completed_at=None,
        )

    def _make_usage(self) -> UsageSummary:
        return UsageSummary(
            total_input_tokens=1000,
            total_output_tokens=500,
            total_cache_read_tokens=0,
            total_cache_creation_tokens=0,
            total_duration_ms=5000,
            record_count=3,
        )

    def test_with_tasks(self) -> None:
        tasks = [self._make_task("Fix bug", "complete"), self._make_task("Add feature")]
        result = format_status(tasks, self._make_usage())
        assert "<b>Status</b>" in result
        assert "Fix bug" in result
        assert "Add feature" in result
        assert "\u2705" in result  # checkmark icon for complete
        assert "\u23f3" in result  # hourglass icon for pending
        assert "1,000" in result
        assert "500" in result

    def test_no_tasks(self) -> None:
        result = format_status([], self._make_usage())
        assert "No tasks." in result

    def test_escapes_task_titles(self) -> None:
        tasks = [self._make_task("<script>alert(1)</script>")]
        result = format_status(tasks, self._make_usage())
        assert "<script>" not in result
        assert "&lt;script&gt;" in result


class TestFormatHelp:
    def test_contains_commands(self) -> None:
        result = format_help()
        assert "/start" in result
        assert "/help" in result
        assert "/status" in result

    def test_is_html(self) -> None:
        result = format_help()
        assert "<b>" in result
