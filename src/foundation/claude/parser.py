"""Async NDJSON parser for Claude CLI stream-json output."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

from foundation.claude.events import (
    AssistantEvent,
    ContentBlock,
    ResultEvent,
    StreamEvent,
    SystemInitEvent,
    TextBlock,
    TokenUsage,
    ToolResultEvent,
    ToolUseBlock,
)


class ParseError(Exception):
    """Raised when a stream-json event cannot be parsed."""


def _parse_usage(raw: dict[str, Any]) -> TokenUsage:
    """Extract token usage from a raw dict."""
    return TokenUsage(
        input_tokens=raw.get("input_tokens", 0),
        output_tokens=raw.get("output_tokens", 0),
        cache_read_input_tokens=raw.get("cache_read_input_tokens", 0),
        cache_creation_input_tokens=raw.get("cache_creation_input_tokens", 0),
    )


def _parse_content_block(raw: dict[str, Any]) -> ContentBlock:
    """Parse a single content block from an assistant message."""
    block_type = raw.get("type", "")
    if block_type == "text":
        return TextBlock(text=raw.get("text", ""))
    elif block_type == "tool_use":
        return ToolUseBlock(
            id=raw.get("id", ""),
            name=raw.get("name", ""),
            input=raw.get("input", {}),
        )
    else:
        # Lenient: treat unknown block types as text with a note
        return TextBlock(text=raw.get("text", f"[unknown block type: {block_type}]"))


def parse_event(raw: dict[str, Any]) -> StreamEvent:
    """Parse a single raw JSON dict into a typed StreamEvent.

    Raises:
        ParseError: If the event type is missing or unknown.
    """
    event_type = raw.get("type")
    if event_type is None:
        raise ParseError(f"Event missing 'type' field: {raw}")

    if event_type == "system":
        return SystemInitEvent(
            session_id=raw.get("session_id", ""),
            tools=raw.get("tools", []),
            model=raw.get("model", ""),
        )

    elif event_type == "assistant":
        message = raw.get("message", {})
        raw_content = message.get("content", [])
        content = [_parse_content_block(b) for b in raw_content]
        usage = _parse_usage(raw.get("usage", {}))
        return AssistantEvent(content=content, usage=usage)

    elif event_type == "tool_result":
        return ToolResultEvent(
            tool_use_id=raw.get("tool_use_id", ""),
            content=raw.get("content", ""),
        )

    elif event_type == "result":
        return ResultEvent(
            session_id=raw.get("session_id", ""),
            result=raw.get("result", ""),
            status=raw.get("status", ""),
            duration_ms=raw.get("duration_ms", 0),
            usage=_parse_usage(raw.get("usage", {})),
        )

    else:
        raise ParseError(f"Unknown event type: {event_type!r}")


async def parse_stream(lines: AsyncIterator[str]) -> AsyncIterator[StreamEvent]:
    """Parse an async stream of NDJSON lines into typed events.

    Blank lines are skipped. JSON decode errors raise ParseError.
    """
    async for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            raw = json.loads(line)
        except json.JSONDecodeError as e:
            raise ParseError(f"Invalid JSON: {e}") from e
        yield parse_event(raw)
