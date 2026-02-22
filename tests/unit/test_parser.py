"""Tests for stream-json parser."""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from foundation.claude.events import (
    AssistantEvent,
    ResultEvent,
    StreamEvent,
    SystemInitEvent,
    TextBlock,
    ToolResultEvent,
    ToolUseBlock,
)
from foundation.claude.parser import ParseError, parse_event, parse_stream

FIXTURES_DIR = Path(__file__).parents[1] / "fixtures" / "stream-json"


async def _lines_from_file(path: Path) -> AsyncIterator[str]:
    """Yield lines from a file as an async iterator."""
    with open(path) as f:
        for line in f:
            yield line


async def _collect(stream: AsyncIterator[StreamEvent]) -> list[StreamEvent]:
    """Collect all events from an async iterator."""
    return [event async for event in stream]


class TestParseEvent:
    def test_system_init(self) -> None:
        raw = {
            "type": "system",
            "subtype": "init",
            "session_id": "abc-123",
            "tools": ["Read"],
            "model": "sonnet",
        }
        event = parse_event(raw)
        assert isinstance(event, SystemInitEvent)
        assert event.session_id == "abc-123"
        assert event.tools == ["Read"]

    def test_assistant_with_text(self) -> None:
        raw = {
            "type": "assistant",
            "message": {"role": "assistant", "content": [{"type": "text", "text": "Hello"}]},
            "usage": {"input_tokens": 10, "output_tokens": 5},
        }
        event = parse_event(raw)
        assert isinstance(event, AssistantEvent)
        assert len(event.content) == 1
        assert isinstance(event.content[0], TextBlock)
        assert event.content[0].text == "Hello"

    def test_assistant_with_tool_use(self) -> None:
        raw = {
            "type": "assistant",
            "message": {
                "role": "assistant",
                "content": [
                    {"type": "tool_use", "id": "t1", "name": "Read", "input": {"file_path": "/x"}}
                ],
            },
            "usage": {"input_tokens": 10, "output_tokens": 5},
        }
        event = parse_event(raw)
        assert isinstance(event, AssistantEvent)
        assert isinstance(event.content[0], ToolUseBlock)
        assert event.content[0].name == "Read"

    def test_tool_result(self) -> None:
        raw = {"type": "tool_result", "tool_use_id": "t1", "content": "file data"}
        event = parse_event(raw)
        assert isinstance(event, ToolResultEvent)
        assert event.tool_use_id == "t1"
        assert event.content == "file data"

    def test_result_success(self) -> None:
        raw = {
            "type": "result",
            "session_id": "abc",
            "result": "Done.",
            "status": "success",
            "duration_ms": 1000,
            "usage": {"input_tokens": 100, "output_tokens": 50},
        }
        event = parse_event(raw)
        assert isinstance(event, ResultEvent)
        assert event.status == "success"
        assert event.usage.input_tokens == 100

    def test_missing_type_raises(self) -> None:
        with pytest.raises(ParseError, match="missing 'type'"):
            parse_event({"foo": "bar"})

    def test_unknown_type_raises(self) -> None:
        with pytest.raises(ParseError, match="Unknown event type"):
            parse_event({"type": "bogus"})


class TestParseStream:
    async def test_simple_response(self) -> None:
        path = FIXTURES_DIR / "simple-response.jsonl"
        events = await _collect(parse_stream(_lines_from_file(path)))
        assert len(events) == 3
        assert isinstance(events[0], SystemInitEvent)
        assert isinstance(events[1], AssistantEvent)
        assert isinstance(events[2], ResultEvent)
        assert events[0].session_id == "test-session-001"
        assert events[2].status == "success"

    async def test_execution_with_tools(self) -> None:
        events = await _collect(
            parse_stream(_lines_from_file(FIXTURES_DIR / "execution-with-tools.jsonl"))
        )
        assert len(events) == 5
        assert isinstance(events[0], SystemInitEvent)
        assert isinstance(events[1], AssistantEvent)
        assert isinstance(events[2], ToolResultEvent)
        assert isinstance(events[3], AssistantEvent)
        assert isinstance(events[4], ResultEvent)

        # Check tool use in first assistant event
        tool_blocks = [b for b in events[1].content if isinstance(b, ToolUseBlock)]
        assert len(tool_blocks) == 1
        assert tool_blocks[0].name == "Read"

    async def test_plan_output(self) -> None:
        events = await _collect(parse_stream(_lines_from_file(FIXTURES_DIR / "plan-output.jsonl")))
        assert len(events) == 3
        assert isinstance(events[0], SystemInitEvent)
        # Plan mode: only read-only tools
        assert "Edit" not in events[0].tools
        result = events[2]
        assert isinstance(result, ResultEvent)
        assert "Plan" in result.result

    async def test_rate_limit_error(self) -> None:
        events = await _collect(
            parse_stream(_lines_from_file(FIXTURES_DIR / "rate-limit-error.jsonl"))
        )
        assert len(events) == 2
        assert isinstance(events[0], SystemInitEvent)
        result = events[1]
        assert isinstance(result, ResultEvent)
        assert result.status == "error"

    async def test_blank_lines_skipped(self) -> None:
        async def _lines() -> AsyncIterator[str]:
            yield '{"type":"system","subtype":"init","session_id":"x"}\n'
            yield "\n"
            yield "  \n"
            yield '{"type":"result","session_id":"x","result":"ok","status":"success"}\n'

        events = await _collect(parse_stream(_lines()))
        assert len(events) == 2

    async def test_invalid_json_raises(self) -> None:
        async def _lines() -> AsyncIterator[str]:
            yield "not json\n"

        with pytest.raises(ParseError, match="Invalid JSON"):
            await _collect(parse_stream(_lines()))
