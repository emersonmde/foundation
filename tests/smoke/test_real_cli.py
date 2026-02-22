"""Smoke tests — real claude CLI invocations. Burns tokens.

Run with: pytest tests/smoke/ --run-smoke -x
"""

from __future__ import annotations

import pytest

from foundation.claude.cli import invoke
from foundation.claude.events import ResultEvent, SystemInitEvent


@pytest.mark.smoke
class TestRealCLI:
    async def test_basic_invocation(self) -> None:
        """Invoke real claude -p and verify we get a structured result."""
        result = await invoke(
            "Respond with exactly the word: pong",
            cli_command="claude",
            model="sonnet",
            permission_mode="plan",
            max_turns=1,
        )
        assert result.session_id != ""
        assert result.status == "success"
        assert result.return_code == 0
        assert "pong" in result.result_text.lower()

    async def test_usage_tokens_present(self) -> None:
        """Verify token usage is captured from a real invocation."""
        result = await invoke(
            "Say hello in one word",
            cli_command="claude",
            model="sonnet",
            permission_mode="plan",
            max_turns=1,
        )
        assert result.usage.input_tokens > 0
        assert result.usage.output_tokens > 0

    async def test_events_contain_init_and_result(self) -> None:
        """Verify we get at least init + result events."""
        result = await invoke(
            "What is 2+2?",
            cli_command="claude",
            model="sonnet",
            permission_mode="plan",
            max_turns=1,
        )
        init_events = [e for e in result.events if isinstance(e, SystemInitEvent)]
        result_events = [e for e in result.events if isinstance(e, ResultEvent)]
        assert len(init_events) >= 1
        assert len(result_events) >= 1
