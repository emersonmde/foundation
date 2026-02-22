"""Telegram message formatting utilities."""

from __future__ import annotations

import html
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from foundation.db.tasks import Task
    from foundation.db.usage import UsageSummary

# Telegram message size limit
MAX_MESSAGE_LENGTH = 4096


def escape_html(text: str) -> str:
    """Escape text for Telegram HTML parse mode."""
    return html.escape(text)


def split_message(text: str, max_length: int = MAX_MESSAGE_LENGTH) -> list[str]:
    """Split text into chunks that fit within Telegram's message limit.

    Splits on newline boundaries when possible, force-splits otherwise.
    Always returns at least one chunk (empty string for empty input).
    """
    if len(text) <= max_length:
        return [text]

    chunks: list[str] = []
    remaining = text

    while remaining:
        if len(remaining) <= max_length:
            chunks.append(remaining)
            break

        # Try to split on a newline within the limit
        split_at = remaining.rfind("\n", 0, max_length)
        if split_at == -1:
            # No newline found — force split at max_length
            split_at = max_length

        chunks.append(remaining[:split_at])
        remaining = remaining[split_at:].lstrip("\n")

    return chunks if chunks else [""]


def format_status(tasks: list[Task], usage: UsageSummary) -> str:
    """Format a status summary as HTML."""
    lines = ["<b>Status</b>", ""]

    if tasks:
        lines.append(f"<b>Tasks:</b> {len(tasks)}")
        for task in tasks[:10]:
            status_icon = {
                "pending": "⏳",
                "planning": "📝",
                "awaiting_approval": "👀",
                "executing": "⚙️",
                "complete": "✅",
                "failed": "❌",
                "cancelled": "🚫",
            }.get(task.status, "❓")
            title = escape_html(task.title[:60])
            lines.append(f"  {status_icon} {title}")
        if len(tasks) > 10:
            lines.append(f"  ... and {len(tasks) - 10} more")
    else:
        lines.append("No tasks.")

    lines.append("")
    lines.append("<b>Usage:</b>")
    lines.append(f"  Sessions: {usage.record_count}")
    lines.append(f"  Input: {usage.total_input_tokens:,} tokens")
    lines.append(f"  Output: {usage.total_output_tokens:,} tokens")

    return "\n".join(lines)


def format_help() -> str:
    """Format the help/command list as HTML."""
    return (
        "<b>Foundation Commands</b>\n"
        "\n"
        "/start — Welcome message\n"
        "/help — Show this help\n"
        "/status — Task and usage summary\n"
        "\n"
        "Send any text message to create a new task."
    )
