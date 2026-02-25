"""MCP tool definitions and handlers for the orchestrator.

Each handler receives validated input and returns a result dict.
Handlers that need messaging or agent management communicate via the IPC bridge.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import aiosqlite

from foundation.db.tasks import Task, create_task, get_task, list_tasks, update_task_status
from foundation.mcp.bridge import BridgeClient
from foundation.mcp.protocol import ToolDefinition

logger = logging.getLogger("foundation.mcp.tools")


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


# --- Tool definitions (returned by tools/list) ---

TOOL_DEFINITIONS: list[ToolDefinition] = [
    ToolDefinition(
        name="create_task",
        description="Create a new development task.",
        input_schema={
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "Short task title"},
                "description": {"type": "string", "description": "Detailed task description"},
            },
            "required": ["title"],
        },
    ),
    ToolDefinition(
        name="update_task",
        description="Update a task's status and optional notes.",
        input_schema={
            "type": "object",
            "properties": {
                "task_id": {"type": "string"},
                "status": {
                    "type": "string",
                    "enum": [
                        "pending",
                        "planning",
                        "awaiting_approval",
                        "executing",
                        "complete",
                        "failed",
                        "cancelled",
                    ],
                },
                "notes": {"type": "string", "description": "Optional result/error text"},
            },
            "required": ["task_id", "status"],
        },
    ),
    ToolDefinition(
        name="query_tasks",
        description="List tasks, optionally filtered by status.",
        input_schema={
            "type": "object",
            "properties": {
                "status": {"type": "string", "description": "Filter by status (optional)"},
                "limit": {"type": "integer", "description": "Max results (default 20)"},
            },
        },
    ),
    ToolDefinition(
        name="send_message",
        description="Send a message to the human via Telegram.",
        input_schema={
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "Message text (HTML supported)"},
                "buttons": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "text": {"type": "string"},
                            "data": {"type": "string"},
                        },
                        "required": ["text", "data"],
                    },
                    "description": "Optional inline buttons",
                },
            },
            "required": ["text"],
        },
    ),
    ToolDefinition(
        name="request_human_input",
        description=(
            "Ask the human a question and wait for their response. "
            "This blocks until the human replies."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "question": {"type": "string", "description": "The question to ask"},
                "options": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "text": {"type": "string"},
                            "data": {"type": "string"},
                        },
                        "required": ["text", "data"],
                    },
                    "description": "Response options as inline buttons",
                },
            },
            "required": ["question", "options"],
        },
    ),
    ToolDefinition(
        name="spawn_agent",
        description="Start a Claude Code sub-agent for a task.",
        input_schema={
            "type": "object",
            "properties": {
                "task_id": {"type": "string"},
                "prompt": {"type": "string", "description": "The prompt for the sub-agent"},
                "mode": {
                    "type": "string",
                    "enum": ["plan", "execute"],
                    "description": "Permission mode (default: plan)",
                },
            },
            "required": ["task_id", "prompt"],
        },
    ),
    ToolDefinition(
        name="cancel_agent",
        description="Cancel a running sub-agent.",
        input_schema={
            "type": "object",
            "properties": {
                "task_id": {"type": "string"},
            },
            "required": ["task_id"],
        },
    ),
    ToolDefinition(
        name="read_memory",
        description="Read a memory/knowledge file by topic.",
        input_schema={
            "type": "object",
            "properties": {
                "topic": {"type": "string", "description": "Memory topic name (becomes filename)"},
            },
            "required": ["topic"],
        },
    ),
    ToolDefinition(
        name="update_memory",
        description="Write or update a memory/knowledge file.",
        input_schema={
            "type": "object",
            "properties": {
                "topic": {"type": "string", "description": "Memory topic name"},
                "content": {"type": "string", "description": "Full content to write"},
            },
            "required": ["topic", "content"],
        },
    ),
    ToolDefinition(
        name="wait",
        description=(
            "Wait for a specified duration. Returns early if a Telegram message arrives. "
            "Use this to pace orchestrator iterations."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "seconds": {
                    "type": "integer",
                    "description": "Seconds to wait (min 30, max 3600)",
                },
            },
            "required": ["seconds"],
        },
    ),
]


def _task_to_dict(task: Task) -> dict[str, Any]:
    """Convert a Task to a JSON-serializable dict."""
    return {
        "id": task.id,
        "title": task.title,
        "description": task.description,
        "status": task.status,
        "created_at": task.created_at,
        "updated_at": task.updated_at,
    }


# --- Tool handlers ---


async def handle_create_task(db: aiosqlite.Connection, params: dict[str, Any]) -> dict[str, Any]:
    """Create a new task."""
    title = params.get("title", "")
    description = params.get("description", "")
    if not title:
        return {"error": "title is required"}
    task = await create_task(db, title, description)
    return {"task": _task_to_dict(task)}


async def handle_update_task(db: aiosqlite.Connection, params: dict[str, Any]) -> dict[str, Any]:
    """Update a task's status."""
    task_id = params.get("task_id", "")
    status = params.get("status", "")
    notes = params.get("notes")

    if not task_id or not status:
        return {"error": "task_id and status are required"}

    task = await get_task(db, task_id)
    if task is None:
        return {"error": f"task {task_id} not found"}

    kwargs: dict[str, str | None] = {}
    if notes is not None:
        if status in ("complete",):
            kwargs["result_text"] = notes
        elif status in ("failed",):
            kwargs["error_message"] = notes
        else:
            kwargs["plan_text"] = notes

    await update_task_status(db, task_id, status, **kwargs)
    updated = await get_task(db, task_id)
    return {"task": _task_to_dict(updated) if updated else None}


async def handle_query_tasks(db: aiosqlite.Connection, params: dict[str, Any]) -> dict[str, Any]:
    """Query tasks with optional filtering."""
    status = params.get("status")
    limit = params.get("limit", 20)
    tasks = await list_tasks(db, status=status)
    tasks = tasks[:limit]
    return {"tasks": [_task_to_dict(t) for t in tasks]}


async def handle_send_message(bridge: BridgeClient, params: dict[str, Any]) -> dict[str, Any]:
    """Send a message to the human via the IPC bridge."""
    text = params.get("text", "")
    buttons = params.get("buttons")
    if not text:
        return {"error": "text is required"}
    result = await bridge.call("send_message", {"text": text, "buttons": buttons})
    return result


async def handle_request_human_input(
    bridge: BridgeClient, params: dict[str, Any]
) -> dict[str, Any]:
    """Ask the human a question and wait for the response."""
    question = params.get("question", "")
    options = params.get("options", [])
    if not question or not options:
        return {"error": "question and options are required"}
    result = await bridge.call("request_human_input", {"question": question, "options": options})
    return result


async def handle_spawn_agent(bridge: BridgeClient, params: dict[str, Any]) -> dict[str, Any]:
    """Spawn a sub-agent via the IPC bridge."""
    task_id = params.get("task_id", "")
    prompt = params.get("prompt", "")
    mode = params.get("mode", "plan")
    if not task_id or not prompt:
        return {"error": "task_id and prompt are required"}
    result = await bridge.call("spawn_agent", {"task_id": task_id, "prompt": prompt, "mode": mode})
    return result


async def handle_cancel_agent(bridge: BridgeClient, params: dict[str, Any]) -> dict[str, Any]:
    """Cancel a running sub-agent via the IPC bridge."""
    task_id = params.get("task_id", "")
    if not task_id:
        return {"error": "task_id is required"}
    result = await bridge.call("cancel_agent", {"task_id": task_id})
    return result


async def handle_read_memory(memory_dir: Path, params: dict[str, Any]) -> dict[str, Any]:
    """Read a memory file."""
    topic = params.get("topic", "")
    if not topic:
        return {"error": "topic is required"}

    # Sanitize: only allow alphanumeric, hyphens, underscores
    safe_topic = "".join(c for c in topic if c.isalnum() or c in "-_")
    if not safe_topic:
        return {"error": "invalid topic name"}

    path = memory_dir / f"{safe_topic}.md"
    if not path.exists():
        return {"content": "", "exists": False}
    return {"content": path.read_text(encoding="utf-8"), "exists": True}


async def handle_update_memory(memory_dir: Path, params: dict[str, Any]) -> dict[str, Any]:
    """Write a memory file."""
    topic = params.get("topic", "")
    content = params.get("content", "")
    if not topic:
        return {"error": "topic is required"}

    safe_topic = "".join(c for c in topic if c.isalnum() or c in "-_")
    if not safe_topic:
        return {"error": "invalid topic name"}

    memory_dir.mkdir(parents=True, exist_ok=True)
    path = memory_dir / f"{safe_topic}.md"
    path.write_text(content, encoding="utf-8")
    return {"status": "updated", "path": str(path)}


async def handle_wait(bridge: BridgeClient, params: dict[str, Any]) -> dict[str, Any]:
    """Wait for a duration, with early wake on incoming messages."""
    seconds = params.get("seconds", 60)
    seconds = max(30, min(3600, seconds))
    result = await bridge.call("wait", {"seconds": seconds})
    return result


async def log_action(
    db: aiosqlite.Connection,
    iteration_id: str,
    action_type: str,
    summary: str,
    details: dict[str, Any] | None = None,
) -> None:
    """Record an action in the action log."""
    await db.execute(
        "INSERT INTO action_log (iteration_id, action_type, summary, details, created_at)"
        " VALUES (?, ?, ?, ?, ?)",
        (iteration_id, action_type, summary, json.dumps(details) if details else None, _now_iso()),
    )
    await db.commit()
