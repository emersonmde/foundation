"""Orchestrator — main event loop and task lifecycle management."""

from __future__ import annotations

import asyncio
import contextlib
import html
import logging

import aiosqlite

from foundation.claude.cli import ClaudeResult, ClaudeSession
from foundation.config import ClaudeConfig
from foundation.db.tasks import (
    Task,
    create_task,
    list_tasks,
    update_task_status,
)
from foundation.db.usage import record_usage
from foundation.messaging.adapter import (
    CallbackResult,
    IncomingMessage,
    InlineButton,
    MessagingAdapter,
)

logger = logging.getLogger(__name__)


class Orchestrator:
    """Drives the task lifecycle: receive → plan → approve → execute → report.

    Concurrency model: two concurrent coroutines sharing the event loop.
    - _message_listener: drains the incoming queue and creates tasks
    - _main_loop: picks up pending tasks and drives them through the lifecycle
    """

    def __init__(
        self,
        claude_config: ClaudeConfig,
        db: aiosqlite.Connection,
        messaging: MessagingAdapter,
        incoming_queue: asyncio.Queue[IncomingMessage],
    ) -> None:
        self._claude_config = claude_config
        self._db = db
        self._messaging = messaging
        self._incoming_queue = incoming_queue
        self._shutdown_event = asyncio.Event()
        self._work_available = asyncio.Event()

    async def run(self) -> None:
        """Start the orchestrator — runs until shutdown() is called."""
        await self._recover_tasks()
        listener = asyncio.create_task(self._message_listener(), name="message-listener")
        main = asyncio.create_task(self._main_loop(), name="main-loop")
        with contextlib.suppress(asyncio.CancelledError):
            await asyncio.gather(listener, main)

    async def shutdown(self) -> None:
        """Signal the orchestrator to stop."""
        self._shutdown_event.set()
        self._work_available.set()  # Unblock the main loop if waiting

    # --- Message listener ---

    async def _message_listener(self) -> None:
        """Drain incoming queue, create tasks in DB."""
        while not self._shutdown_event.is_set():
            try:
                msg = await asyncio.wait_for(self._incoming_queue.get(), timeout=1.0)
            except TimeoutError:
                continue

            try:
                title = msg.text[:100]
                description = msg.text
                task = await create_task(self._db, title, description)
                logger.info("New task %s: %s", task.id[:8], title)
                await self._messaging.send_message(
                    f"Task created: <b>{html.escape(title)}</b>\n<code>{task.id[:8]}</code>",
                )
                self._work_available.set()
            except Exception:
                logger.exception("Failed to create task from message: %s", msg.text[:50])

    # --- Main loop ---

    async def _main_loop(self) -> None:
        """Pick up pending tasks and drive them through the lifecycle."""
        while not self._shutdown_event.is_set():
            # Wait for work or shutdown
            self._work_available.clear()
            pending = await list_tasks(self._db, status="pending")
            if not pending:
                await self._work_available.wait()
                if self._shutdown_event.is_set():
                    break
                continue

            # Process the oldest pending task (list_tasks returns newest first)
            task = pending[-1]
            await self._run_task_lifecycle(task)

    # --- Task lifecycle ---

    async def _run_task_lifecycle(self, task: Task) -> None:
        """Drive a single task through: planning → approval → execution."""
        try:
            # Phase 1: Planning
            plan_result = await self._run_planning(task)
            if plan_result is None:
                return  # Task failed during planning

            plan_text = plan_result.result_text
            session_id = plan_result.session_id

            # Phase 2: Approval loop (may re-plan on modify)
            max_modify_rounds = 10
            for _round in range(max_modify_rounds):
                decision = await self._request_approval(task, plan_text)

                if decision == "approve":
                    break
                elif decision == "reject":
                    await update_task_status(self._db, task.id, "cancelled")
                    await self._messaging.send_message(
                        f"Task cancelled: <b>{html.escape(task.title)}</b>"
                    )
                    return
                else:
                    # Modify: decision is the feedback text
                    modify_result = await self._run_replan(task, session_id, decision)
                    if modify_result is None:
                        return  # Re-plan failed
                    plan_text = modify_result.result_text
                    session_id = modify_result.session_id

            else:
                # Exhausted modify rounds without approval
                await self._notify_failure(task, "Too many modify rounds without approval")
                return

            # Phase 3: Execution
            await self._run_execution(task, session_id)

        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Unhandled error in task %s lifecycle", task.id[:8])
            await self._notify_failure(task, "Internal orchestrator error")

    async def _run_planning(self, task: Task) -> ClaudeResult | None:
        """Run planning phase: invoke claude with plan permission mode."""
        await update_task_status(self._db, task.id, "planning")

        try:
            session = ClaudeSession(
                task.description,
                cli_command=self._claude_config.cli_command,
                model=self._claude_config.model,
                permission_mode=self._claude_config.plan_permission_mode,
                max_turns=self._claude_config.max_turns,
            )
            result = await session.run()
        except Exception as e:
            logger.error("Planning failed for task %s: %s", task.id[:8], e)
            await self._notify_failure(task, f"Planning failed: {e}")
            return None

        await self._record_usage(task, result, "orchestrator")

        if result.status != "success":
            await self._notify_failure(task, f"Planning failed: {result.result_text}")
            return None

        await update_task_status(
            self._db,
            task.id,
            "awaiting_approval",
            session_id=result.session_id,
            plan_text=result.result_text,
        )
        return result

    async def _request_approval(self, task: Task, plan_text: str) -> str:
        """Send plan for approval and wait for response.

        Returns "approve", "reject", or the modification feedback text.
        """
        # Truncate plan text to avoid exceeding Telegram message limits
        max_plan_chars = 3500
        display_plan = plan_text[:max_plan_chars]
        if len(plan_text) > max_plan_chars:
            display_plan += "\n\n<i>[truncated]</i>"

        msg_id = await self._messaging.send_message(
            f"<b>Plan for:</b> {html.escape(task.title)}\n\n{html.escape(display_plan)}",
            buttons=[
                [
                    InlineButton(text="Approve", callback_data="approve"),
                    InlineButton(text="Reject", callback_data="reject"),
                    InlineButton(text="Modify", callback_data="modify"),
                ]
            ],
        )

        callback: CallbackResult = await self._messaging.wait_for_callback(msg_id)

        # Remove buttons after decision
        await self._messaging.edit_message(
            msg_id,
            f"<b>Plan for:</b> {html.escape(task.title)}\n\n"
            f"{html.escape(display_plan)}\n\n"
            f"<i>Decision: {html.escape(callback.data)}</i>",
        )

        if callback.data == "modify":
            await self._messaging.send_message("Send your feedback for the plan:")
            feedback = await self._messaging.wait_for_reply()
            return feedback

        return callback.data

    async def _run_replan(self, task: Task, session_id: str, feedback: str) -> ClaudeResult | None:
        """Resume the planning session with modification feedback."""
        await update_task_status(self._db, task.id, "planning")

        try:
            session = ClaudeSession(
                feedback,
                cli_command=self._claude_config.cli_command,
                model=self._claude_config.model,
                permission_mode=self._claude_config.plan_permission_mode,
                max_turns=self._claude_config.max_turns,
                resume_session_id=session_id,
            )
            result = await session.run()
        except Exception as e:
            logger.error("Re-planning failed for task %s: %s", task.id[:8], e)
            await self._notify_failure(task, f"Re-planning failed: {e}")
            return None

        await self._record_usage(task, result, "orchestrator")

        if result.status != "success":
            await self._notify_failure(task, f"Re-planning failed: {result.result_text}")
            return None

        await update_task_status(
            self._db,
            task.id,
            "awaiting_approval",
            session_id=result.session_id,
            plan_text=result.result_text,
        )
        return result

    async def _run_execution(self, task: Task, session_id: str) -> None:
        """Resume the session with execution permissions."""
        await update_task_status(self._db, task.id, "executing")
        await self._messaging.send_message(f"Executing: <b>{html.escape(task.title)}</b>")

        try:
            session = ClaudeSession(
                "Proceed with the plan.",
                cli_command=self._claude_config.cli_command,
                model=self._claude_config.model,
                permission_mode=self._claude_config.execute_permission_mode,
                max_turns=self._claude_config.max_turns,
                resume_session_id=session_id,
            )
            result = await session.run()
        except Exception as e:
            logger.error("Execution failed for task %s: %s", task.id[:8], e)
            await self._notify_failure(task, f"Execution failed: {e}")
            return

        await self._record_usage(task, result, "coding")

        if result.status == "success":
            await self._notify_completion(task, result)
        else:
            await self._notify_failure(task, f"Execution failed: {result.result_text}")

    # --- Notifications ---

    async def _notify_completion(self, task: Task, result: ClaudeResult) -> None:
        """Mark task complete and notify the user."""
        await update_task_status(
            self._db,
            task.id,
            "complete",
            session_id=result.session_id,
            result_text=result.result_text,
        )
        await self._messaging.send_message(
            f"Task complete: <b>{html.escape(task.title)}</b>\n\n"
            f"{html.escape(result.result_text[:500])}",
        )
        logger.info("Task %s completed", task.id[:8])

    async def _notify_failure(self, task: Task, error: str) -> None:
        """Mark task failed and notify the user."""
        await update_task_status(
            self._db,
            task.id,
            "failed",
            error_message=error,
        )
        await self._messaging.send_message(
            f"Task failed: <b>{html.escape(task.title)}</b>\n\n{html.escape(error[:500])}",
        )
        logger.error("Task %s failed: %s", task.id[:8], error)

    # --- Usage tracking ---

    async def _record_usage(self, task: Task, result: ClaudeResult, category: str) -> None:
        """Record token usage for a Claude invocation."""
        await record_usage(
            self._db,
            task_id=task.id,
            session_id=result.session_id,
            category=category,
            model=self._claude_config.model,
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
        - awaiting_approval → reset to pending (plan_text preserved, will be re-planned)
        - executing → mark failed (interrupted execution is not resumable)
        """
        all_tasks = await list_tasks(self._db)
        recovered = False

        for task in all_tasks:
            if task.status == "planning":
                logger.info("Recovering task %s: planning → pending", task.id[:8])
                await update_task_status(self._db, task.id, "pending")
                recovered = True

            elif task.status == "awaiting_approval":
                logger.info("Recovering task %s: awaiting_approval → pending", task.id[:8])
                await update_task_status(self._db, task.id, "pending")
                recovered = True

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

        if recovered:
            self._work_available.set()
