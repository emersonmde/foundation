"""Tests for stream-json event dataclasses."""

from __future__ import annotations

from foundation.claude.events import (
    AssistantEvent,
    PermissionDenial,
    ResultEvent,
    SystemInitEvent,
    TextBlock,
    TokenUsage,
    ToolResultEvent,
    ToolUseBlock,
)


class TestContentBlocks:
    def test_text_block(self) -> None:
        b = TextBlock(text="hello")
        assert b.text == "hello"
        assert b.type == "text"

    def test_tool_use_block(self) -> None:
        b = ToolUseBlock(id="toolu_1", name="Read", input={"file_path": "/src/main.py"})
        assert b.name == "Read"
        assert b.input["file_path"] == "/src/main.py"
        assert b.type == "tool_use"


class TestTokenUsage:
    def test_defaults(self) -> None:
        u = TokenUsage()
        assert u.input_tokens == 0
        assert u.output_tokens == 0
        assert u.cache_read_input_tokens == 0
        assert u.cache_creation_input_tokens == 0

    def test_with_values(self) -> None:
        u = TokenUsage(input_tokens=100, output_tokens=50, cache_read_input_tokens=20)
        assert u.input_tokens == 100
        assert u.output_tokens == 50
        assert u.cache_read_input_tokens == 20
        assert u.cache_creation_input_tokens == 0


class TestEvents:
    def test_system_init(self) -> None:
        e = SystemInitEvent(session_id="abc-123", tools=["Read", "Edit"], model="sonnet")
        assert e.session_id == "abc-123"
        assert e.model == "sonnet"
        assert "Read" in e.tools

    def test_assistant_event(self) -> None:
        content: list[TextBlock | ToolUseBlock] = [
            TextBlock(text="Hello"),
            ToolUseBlock(id="t1", name="Bash", input={"cmd": "ls"}),
        ]
        usage = TokenUsage(input_tokens=10, output_tokens=5)
        e = AssistantEvent(content=content, usage=usage)
        assert len(e.content) == 2
        assert e.usage.input_tokens == 10

    def test_tool_result_event(self) -> None:
        e = ToolResultEvent(tool_use_id="toolu_1", content="file contents")
        assert e.tool_use_id == "toolu_1"
        assert e.content == "file contents"

    def test_result_event(self) -> None:
        e = ResultEvent(
            session_id="abc-123",
            result="Done.",
            status="success",
            duration_ms=1234,
            usage=TokenUsage(input_tokens=100, output_tokens=50),
        )
        assert e.session_id == "abc-123"
        assert e.result == "Done."
        assert e.status == "success"
        assert e.duration_ms == 1234
        assert e.usage.input_tokens == 100
        assert e.permission_denials == []

    def test_result_event_with_permission_denials(self) -> None:
        denials = [
            PermissionDenial(
                tool_name="Edit",
                tool_use_id="toolu_deny_1",
                tool_input={"file_path": "/x", "old_string": "a", "new_string": "b"},
            ),
        ]
        e = ResultEvent(
            session_id="abc-123",
            result="Denied.",
            status="success",
            permission_denials=denials,
        )
        assert len(e.permission_denials) == 1
        assert e.permission_denials[0].tool_name == "Edit"
        assert e.permission_denials[0].tool_use_id == "toolu_deny_1"


class TestPermissionDenial:
    def test_creation(self) -> None:
        d = PermissionDenial(
            tool_name="Bash",
            tool_use_id="toolu_1",
            tool_input={"command": "rm -rf /"},
        )
        assert d.tool_name == "Bash"
        assert d.tool_use_id == "toolu_1"
        assert d.tool_input["command"] == "rm -rf /"
