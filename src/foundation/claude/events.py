"""Stream-json event dataclasses (frozen) for Claude CLI output."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# --- Content blocks ---


@dataclass(frozen=True)
class TextBlock:
    """A text content block in an assistant message."""

    text: str
    type: str = "text"


@dataclass(frozen=True)
class ToolUseBlock:
    """A tool_use content block in an assistant message."""

    id: str
    name: str
    input: dict[str, Any]
    type: str = "tool_use"


ContentBlock = TextBlock | ToolUseBlock


# --- Token usage ---


@dataclass(frozen=True)
class TokenUsage:
    """Token usage counters from a Claude response."""

    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_input_tokens: int = 0
    cache_creation_input_tokens: int = 0


# --- Event types ---


@dataclass(frozen=True)
class SystemInitEvent:
    """Emitted once at session start."""

    session_id: str
    tools: list[str] = field(default_factory=list)
    model: str = ""


@dataclass(frozen=True)
class AssistantEvent:
    """Model output — text and/or tool calls."""

    content: list[ContentBlock] = field(default_factory=list)
    usage: TokenUsage = field(default_factory=TokenUsage)


@dataclass(frozen=True)
class ToolResultEvent:
    """Output from tool execution."""

    tool_use_id: str
    content: str = ""


@dataclass(frozen=True)
class PermissionDenial:
    """A tool call that was denied by the permission system."""

    tool_name: str
    tool_use_id: str
    tool_input: dict[str, Any]


@dataclass(frozen=True)
class ResultEvent:
    """Emitted once at completion."""

    session_id: str
    result: str
    status: str
    duration_ms: int = 0
    usage: TokenUsage = field(default_factory=TokenUsage)
    permission_denials: list[PermissionDenial] = field(default_factory=list)


StreamEvent = SystemInitEvent | AssistantEvent | ToolResultEvent | ResultEvent
