"""Orchestrator — fresh-session agent loop with MCP tools (Milestone 0.4).

Replaces the procedural state machine from Milestone 0.3. Each iteration:
1. Build state snapshot from DB, agent registry, pending messages
2. Start a fresh claude -p session with MCP tools
3. Claude decides what to do, calls tools via the MCP server
4. Session ends, state persists in SQLite + markdown
5. Repeat
"""

from __future__ import annotations

import asyncio
import contextlib
import html
import json
import logging
import tempfile
import uuid
from datetime import UTC, datetime
from pathlib import Path

import aiosqlite

from foundation.agents.registry import AgentRegistry
from foundation.claude.cli import ClaudeResult, ClaudeSession
from foundation.config import Config
from foundation.db.tasks import (
    list_tasks,
    update_task_status,
)
from foundation.db.usage import record_usage
from foundation.mcp.bridge import BridgeServer
from foundation.messaging.adapter import (
    CallbackResult,
    IncomingMessage,
    InlineButton,
    MessagingAdapter,
)
from foundation.state import build_state_snapshot

logger = logging.getLogger(__name__)

# Default allowed built-in tools for the orchestrator session
_DEFAULT_ALLOWED_TOOLS = ["Read", "Glob", "Grep"]


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _load_system_prompt() -> str:
    """Load the orchestrator system prompt from the prompts directory."""
    prompt_path = Path(__file__).parent / "prompts" / "orchestrator.md"
    if prompt_path.exists():
        return prompt_path.read_text(encoding="utf-8")
    return "You are Foundation's orchestrator. Manage tasks and sub-agents."


class Orchestrator:
    """Fresh-session agent loop — Claude decides, Python executes.

    Concurrency model: two concurrent coroutines sharing the event loop.
    - _message_listener: drains the incoming queue, stores pending messages, wakes wait
    - _iteration_loop: runs fresh-session iterations
    """

    def __init__(
        self,
        config: Config,
        db: aiosqlite.Connection,
        messaging: MessagingAdapter,
        incoming_queue: asyncio.Queue[IncomingMessage],
    ) -> None:
        self._config = config
        self._db = db
        self._messaging = messaging
        self._incoming_queue = incoming_queue
        self._shutdown_event = asyncio.Event()
        self._wake_event = asyncio.Event()
        self._agent_registry = AgentRegistry()
        self._bridge = BridgeServer(self._ipc_socket_path)
        self._mcp_config_dir = Path(tempfile.mkdtemp(prefix="foundation-mcp-"))

    @property
    def _ipc_socket_path(self) -> Path:
        return self._config.foundation.data_dir / "foundation-ipc.sock"

    @property
    def _memory_dir(self) -> Path:
        return self._config.foundation.data_dir / "memory"

    @property
    def _db_path(self) -> Path:
        return self._config.foundation.data_dir / "foundation.db"

    async def run(self) -> None:
        """Start the orchestrator — runs until shutdown() is called."""
        await self._recover_tasks()
        self._register_bridge_handlers()
        await self._bridge.start()

        listener = asyncio.create_task(self._message_listener(), name="message-listener")
        loop = asyncio.create_task(self._iteration_loop(), name="iteration-loop")

        with contextlib.suppress(asyncio.CancelledError):
            await asyncio.gather(listener, loop)

        await self._bridge.stop()

    async def shutdown(self) -> None:
        """Signal the orchestrator to stop."""
        self._shutdown_event.set()
        self._wake_event.set()

    # --- Bridge handler registration ---

    def _register_bridge_handlers(self) -> None:
        """Register IPC bridge handlers for messaging, agents, and wait."""
        self._bridge.register("send_message", self._bridge_send_message)
        self._bridge.register("edit_message", self._bridge_edit_message)
        self._bridge.register("request_human_input", self._bridge_request_human_input)
        self._bridge.register("spawn_agent", self._bridge_spawn_agent)
        self._bridge.register("cancel_agent", self._bridge_cancel_agent)
        self._bridge.register("query_agents", self._bridge_query_agents)
        self._bridge.register("wait", self._bridge_wait)

    async def _bridge_send_message(self, params: dict) -> dict:  # type: ignore[type-arg]
        text = params.get("text", "")
        buttons_raw = params.get("buttons")
        buttons = None
        if buttons_raw:
            buttons = [
                [InlineButton(text=b["text"], callback_data=b["data"]) for b in buttons_raw]
            ]
        msg_id = await self._messaging.send_message(text, buttons=buttons)
        return {"message_id": msg_id}

    async def _bridge_edit_message(self, params: dict) -> dict:  # type: ignore[type-arg]
        msg_id = params["message_id"]
        text = params.get("text", "")
        await self._messaging.edit_message(msg_id, text)
        return {"status": "edited"}

    async def _bridge_request_human_input(self, params: dict) -> dict:  # type: ignore[type-arg]
        question = params["question"]
        options = params["options"]
        buttons = [[InlineButton(text=o["text"], callback_data=o["data"]) for o in options]]
        msg_id = await self._messaging.send_message(question, buttons=buttons)
        callback: CallbackResult = await self._messaging.wait_for_callback(msg_id)
        # Remove buttons after answer
        await self._messaging.edit_message(
            msg_id, f"{question}\n\n<i>Answer: {html.escape(callback.data)}</i>"
        )
        return {"answer": callback.data}

    async def _bridge_spawn_agent(self, params: dict) -> dict:  # type: ignore[type-arg]
        task_id = params["task_id"]
        prompt = params["prompt"]
        mode = params.get("mode", "plan")
        try:
            await self._agent_registry.spawn(
                task_id,
                prompt,
                mode=mode,
                cli_command=self._config.claude.cli_command,
                model=self._config.claude.model,
            )
            # Record in DB
            await self._db.execute(
                "INSERT INTO agent_sessions (task_id, mode, status, started_at)"
                " VALUES (?, ?, ?, ?)",
                (task_id, mode, "running", _now_iso()),
            )
            await self._db.commit()
            return {"status": "spawned", "task_id": task_id}
        except ValueError as e:
            return {"error": str(e)}

    async def _bridge_cancel_agent(self, params: dict) -> dict:  # type: ignore[type-arg]
        task_id = params["task_id"]
        cancelled = await self._agent_registry.cancel(task_id)
        if cancelled:
            await self._db.execute(
                "UPDATE agent_sessions SET status = 'cancelled', completed_at = ?"
                " WHERE task_id = ? AND status = 'running'",
                (_now_iso(), task_id),
            )
            await self._db.commit()
        return {"cancelled": cancelled}

    async def _bridge_query_agents(self, params: dict) -> dict:  # type: ignore[type-arg]
        active = self._agent_registry.list_active()
        return {"agents": [a.to_dict() for a in active]}

    async def _bridge_wait(self, params: dict) -> dict:  # type: ignore[type-arg]
        seconds = params.get("seconds", 60)
        seconds = max(30, min(3600, seconds))

        self._wake_event.clear()
        try:
            await asyncio.wait_for(self._wake_event.wait(), timeout=seconds)
            return {"reason": "message_received"}
        except TimeoutError:
            return {"reason": "timeout"}

    # --- Message listener ---

    async def _message_listener(self) -> None:
        """Drain incoming queue, store in pending_messages, wake the wait."""
        while not self._shutdown_event.is_set():
            try:
                msg = await asyncio.wait_for(self._incoming_queue.get(), timeout=1.0)
            except TimeoutError:
                continue

            try:
                await self._db.execute(
                    "INSERT INTO pending_messages (text, received_at) VALUES (?, ?)",
                    (msg.text, _now_iso()),
                )
                await self._db.commit()
                logger.info("Stored pending message: %s", msg.text[:50])
                self._wake_event.set()
            except Exception:
                logger.exception("Failed to store pending message")

    # --- Iteration loop ---

    async def _iteration_loop(self) -> None:
        """Run fresh-session iterations until shutdown."""
        while not self._shutdown_event.is_set():
            try:
                await self._run_iteration()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Iteration failed")
                # Back off on errors to avoid tight loop
                await asyncio.sleep(5.0)

    async def _run_iteration(self) -> None:
        """Single orchestrator iteration: snapshot → claude -p → done."""
        iteration_id = str(uuid.uuid4())[:8]

        # Collect finished agents before building snapshot
        finished = self._agent_registry.collect_finished()
        for task_id, result in finished:
            await self._db.execute(
                "UPDATE agent_sessions SET status = ?, result_text = ?, completed_at = ?"
                " WHERE task_id = ? AND status = 'running'",
                (
                    "completed" if result.status == "success" else "failed",
                    result.result_text[:2000] if result.result_text else "",
                    _now_iso(),
                    task_id,
                ),
            )
            await self._db.commit()
            await self._record_usage(result, task_id=task_id, category="coding")

        snapshot = await build_state_snapshot(self._db, self._agent_registry)
        system_prompt = _load_system_prompt() + "\n\n" + snapshot

        mcp_config_path = self._write_mcp_config()

        try:
            session = ClaudeSession(
                "Review the current state and decide what to do next.",
                cli_command=self._config.claude.cli_command,
                model=self._config.claude.model,
                permission_mode="plan",
                max_turns=self._config.claude.max_turns,
                mcp_config=str(mcp_config_path),
                strict_mcp=True,
                allowed_tools=_DEFAULT_ALLOWED_TOOLS,
                append_system_prompt=system_prompt,
            )

            result = await session.run()
            await self._record_usage(result, category="orchestrator")

            # Log what happened
            await self._db.execute(
                "INSERT INTO action_log (iteration_id, action_type, summary, details, created_at)"
                " VALUES (?, ?, ?, ?, ?)",
                (
                    iteration_id,
                    "iteration",
                    f"status={result.status}, events={len(result.events)}",
                    json.dumps({"result_text": result.result_text[:500]}),
                    _now_iso(),
                ),
            )
            await self._db.commit()

            # Mark processed messages
            await self._db.execute("UPDATE pending_messages SET processed = 1 WHERE processed = 0")
            await self._db.commit()

            # Handle permission denials
            if result.permission_denials:
                await self._forward_permission_denials(result)

        finally:
            mcp_config_path.unlink(missing_ok=True)

    # --- Permission denial forwarding ---

    async def _forward_permission_denials(self, result: ClaudeResult) -> None:
        """Forward permission denials to the human via Telegram."""
        for denial in result.permission_denials:
            tool_desc = denial.tool_name
            input_preview = json.dumps(denial.tool_input, indent=2)[:500]
            msg = (
                f"<b>Permission denied:</b> {html.escape(tool_desc)}\n"
                f"<pre>{html.escape(input_preview)}</pre>"
            )
            await self._messaging.send_message(msg)
            logger.warning("Permission denied: %s", denial.tool_name)

    # --- MCP config generation ---

    def _write_mcp_config(self) -> Path:
        """Write a temporary MCP config file for claude -p."""
        config = {
            "mcpServers": {
                "foundation": {
                    "command": "python",
                    "args": ["-m", "foundation.mcp.server"],
                    "env": {
                        "FOUNDATION_DB_PATH": str(self._db_path),
                        "FOUNDATION_IPC_SOCKET": str(self._ipc_socket_path),
                        "FOUNDATION_MEMORY_DIR": str(self._memory_dir),
                    },
                }
            }
        }
        path = self._mcp_config_dir / "mcp-config.json"
        path.write_text(json.dumps(config), encoding="utf-8")
        return path

    # --- Usage tracking ---

    async def _record_usage(
        self,
        result: ClaudeResult,
        *,
        task_id: str | None = None,
        category: str = "orchestrator",
    ) -> None:
        """Record token usage for a Claude invocation."""
        await record_usage(
            self._db,
            task_id=task_id,
            session_id=result.session_id,
            category=category,
            model=self._config.claude.model,
            input_tokens=result.usage.input_tokens,
            output_tokens=result.usage.output_tokens,
            cache_read_tokens=result.usage.cache_read_input_tokens,
            cache_creation_tokens=result.usage.cache_creation_input_tokens,
            duration_ms=result.duration_ms,
        )

    # --- Startup recovery ---

    async def _recover_tasks(self) -> None:
        """Recover non-terminal tasks on startup.

        - planning → reset to pending (will be re-planned)
        - awaiting_approval → reset to pending
        - executing → mark failed (interrupted execution is not resumable)
        """
        all_tasks = await list_tasks(self._db)

        for task in all_tasks:
            if task.status == "planning":
                logger.info("Recovering task %s: planning → pending", task.id[:8])
                await update_task_status(self._db, task.id, "pending")

            elif task.status == "awaiting_approval":
                logger.info("Recovering task %s: awaiting_approval → pending", task.id[:8])
                await update_task_status(self._db, task.id, "pending")

            elif task.status == "executing":
                logger.info(
                    "Recovering task %s: executing → failed (interrupted)",
                    task.id[:8],
                )
                await update_task_status(
                    self._db,
                    task.id,
                    "failed",
                    error_message="Interrupted during execution (orchestrator restart)",
                )
                await self._messaging.send_message(
                    f"Task interrupted: <b>{html.escape(task.title)}</b>\n"
                    "Execution was interrupted by orchestrator restart.",
                )

        # Mark any running agent sessions as failed on restart
        await self._db.execute(
            "UPDATE agent_sessions SET status = 'failed', completed_at = ?"
            " WHERE status = 'running'",
            (_now_iso(),),
        )
        await self._db.commit()
