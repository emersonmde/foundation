"""Integration tests for Claude CLI wrapper using the stub."""

from __future__ import annotations

from pathlib import Path

import pytest

from foundation.claude.cli import ClaudeSession, invoke
from foundation.claude.events import (
    AssistantEvent,
    ResultEvent,
    SystemInitEvent,
    ToolResultEvent,
    ToolUseBlock,
)


@pytest.fixture(autouse=True)
def _stub_env(monkeypatch: pytest.MonkeyPatch, fixture_dir: Path) -> None:
    """Set up environment for stub usage."""
    monkeypatch.setenv("CLAUDE_STUB_FIXTURE_DIR", str(fixture_dir))


class TestClaudeSession:
    async def test_build_command(self) -> None:
        session = ClaudeSession(
            "hello",
            cli_command="claude",
            model="sonnet",
            permission_mode="plan",
        )
        cmd = session._build_command()
        assert cmd[0] == "claude"
        assert "-p" in cmd
        assert "--output-format" in cmd
        assert "stream-json" in cmd
        assert "--verbose" in cmd
        assert cmd[-1] == "hello"

    async def test_build_command_with_resume(self) -> None:
        session = ClaudeSession(
            "continue",
            cli_command="claude",
            resume_session_id="sess-123",
        )
        cmd = session._build_command()
        assert "--resume" in cmd
        idx = cmd.index("--resume")
        assert cmd[idx + 1] == "sess-123"

    async def test_build_command_with_max_turns(self) -> None:
        session = ClaudeSession("hello", max_turns=5)
        cmd = session._build_command()
        assert "--max-turns" in cmd
        idx = cmd.index("--max-turns")
        assert cmd[idx + 1] == "5"

    async def test_build_command_with_mcp_config(self) -> None:
        session = ClaudeSession(
            "hello",
            mcp_config="/tmp/mcp.json",
        )
        cmd = session._build_command()
        assert "--mcp-config" in cmd
        idx = cmd.index("--mcp-config")
        assert cmd[idx + 1] == "/tmp/mcp.json"

    async def test_build_command_with_strict_mcp(self) -> None:
        session = ClaudeSession(
            "hello",
            mcp_config="/tmp/mcp.json",
            strict_mcp=True,
        )
        cmd = session._build_command()
        assert "--strict-mcp-config" in cmd

    async def test_build_command_with_allowed_tools(self) -> None:
        session = ClaudeSession(
            "hello",
            allowed_tools=["Read", "Glob", "Grep"],
        )
        cmd = session._build_command()
        assert "--allowedTools" in cmd
        idx = cmd.index("--allowedTools")
        assert cmd[idx + 1] == "Read,Glob,Grep"

    async def test_build_command_with_append_system_prompt(self) -> None:
        session = ClaudeSession(
            "hello",
            append_system_prompt="You are an SDM.",
        )
        cmd = session._build_command()
        assert "--append-system-prompt" in cmd
        idx = cmd.index("--append-system-prompt")
        assert cmd[idx + 1] == "You are an SDM."

    async def test_build_command_prompt_always_last(self) -> None:
        session = ClaudeSession(
            "the prompt",
            mcp_config="/tmp/mcp.json",
            strict_mcp=True,
            allowed_tools=["Read"],
            append_system_prompt="extra context",
            extra_flags=["--some-flag"],
        )
        cmd = session._build_command()
        assert cmd[-1] == "the prompt"

    async def test_clean_env_strips_claude_vars(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("CLAUDECODE", "1")
        monkeypatch.setenv("CLAUDE_CODE_ENTRYPOINT", "test")
        env = ClaudeSession._clean_env()
        assert "CLAUDECODE" not in env
        assert "CLAUDE_CODE_ENTRYPOINT" not in env
        assert "PATH" in env


class TestInvokeWithStub:
    async def test_success_scenario(
        self, monkeypatch: pytest.MonkeyPatch, stub_command: str
    ) -> None:
        monkeypatch.setenv("CLAUDE_STUB_SCENARIO", "success")
        result = await invoke(
            "Say hello",
            cli_command=stub_command,
            permission_mode="plan",
        )
        assert result.status == "success"
        assert result.return_code == 0
        assert result.session_id == "stub-session-001"
        assert "ready to help" in result.result_text

    async def test_error_scenario(
        self, monkeypatch: pytest.MonkeyPatch, stub_command: str
    ) -> None:
        monkeypatch.setenv("CLAUDE_STUB_SCENARIO", "error")
        result = await invoke(
            "Will fail",
            cli_command=stub_command,
            permission_mode="plan",
        )
        assert result.status == "error"
        assert result.return_code == 1

    async def test_tools_scenario(
        self, monkeypatch: pytest.MonkeyPatch, stub_command: str
    ) -> None:
        monkeypatch.setenv("CLAUDE_STUB_SCENARIO", "tools")
        result = await invoke(
            "Read a file",
            cli_command=stub_command,
            permission_mode="plan",
        )
        assert result.status == "success"
        assert len(result.events) == 5
        assert isinstance(result.events[0], SystemInitEvent)
        assert isinstance(result.events[1], AssistantEvent)
        assert isinstance(result.events[2], ToolResultEvent)
        assert isinstance(result.events[3], AssistantEvent)
        assert isinstance(result.events[4], ResultEvent)

    async def test_plan_scenario(self, monkeypatch: pytest.MonkeyPatch, stub_command: str) -> None:
        monkeypatch.setenv("CLAUDE_STUB_SCENARIO", "plan")
        result = await invoke(
            "Make a plan",
            cli_command=stub_command,
            permission_mode="plan",
        )
        assert result.status == "success"
        assert "Plan" in result.result_text

    async def test_session_id_override(
        self, monkeypatch: pytest.MonkeyPatch, stub_command: str
    ) -> None:
        monkeypatch.setenv("CLAUDE_STUB_SCENARIO", "success")
        monkeypatch.setenv("CLAUDE_STUB_SESSION_ID", "my-custom-id")
        result = await invoke(
            "Hello",
            cli_command=stub_command,
        )
        assert result.session_id == "my-custom-id"

    async def test_usage_tokens_captured(
        self, monkeypatch: pytest.MonkeyPatch, stub_command: str
    ) -> None:
        monkeypatch.setenv("CLAUDE_STUB_SCENARIO", "success")
        result = await invoke(
            "Hello",
            cli_command=stub_command,
        )
        assert result.usage.input_tokens > 0
        assert result.usage.output_tokens > 0

    async def test_permission_denied_scenario(
        self, monkeypatch: pytest.MonkeyPatch, stub_command: str
    ) -> None:
        monkeypatch.setenv("CLAUDE_STUB_SCENARIO", "permission_denied")
        result = await invoke(
            "Edit a file",
            cli_command=stub_command,
            permission_mode="plan",
        )
        assert result.status == "success"
        assert len(result.permission_denials) == 2
        assert result.permission_denials[0].tool_name == "Edit"
        assert result.permission_denials[0].tool_use_id == "toolu_deny_1"
        assert result.permission_denials[1].tool_name == "Bash"

    async def test_orchestrator_scenario(
        self, monkeypatch: pytest.MonkeyPatch, stub_command: str
    ) -> None:
        monkeypatch.setenv("CLAUDE_STUB_SCENARIO", "orchestrator")
        result = await invoke(
            "Check state and decide",
            cli_command=stub_command,
            permission_mode="plan",
        )
        assert result.status == "success"
        assert len(result.events) == 6
        # Verify MCP tool calls show up as ToolUseBlock
        assistant_events = [e for e in result.events if isinstance(e, AssistantEvent)]
        assert len(assistant_events) == 2
        tool_blocks = [
            b for e in assistant_events for b in e.content if isinstance(b, ToolUseBlock)
        ]
        assert any(b.name == "mcp__foundation__spawn_agent" for b in tool_blocks)
        assert any(b.name == "mcp__foundation__wait" for b in tool_blocks)

    async def test_no_permission_denials_by_default(
        self, monkeypatch: pytest.MonkeyPatch, stub_command: str
    ) -> None:
        monkeypatch.setenv("CLAUDE_STUB_SCENARIO", "success")
        result = await invoke(
            "Hello",
            cli_command=stub_command,
        )
        assert result.permission_denials == []

    async def test_streaming(self, monkeypatch: pytest.MonkeyPatch, stub_command: str) -> None:
        monkeypatch.setenv("CLAUDE_STUB_SCENARIO", "success")
        session = ClaudeSession(
            "Hello",
            cli_command=stub_command,
        )
        events = [event async for event in session.run_streaming()]
        assert len(events) == 3
        assert isinstance(events[0], SystemInitEvent)
