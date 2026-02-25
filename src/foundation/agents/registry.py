"""Sub-agent process registry — tracks running Claude Code sub-agents."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from dataclasses import dataclass, field
from typing import Any

from foundation.claude.cli import ClaudeResult, ClaudeSession

logger = logging.getLogger("foundation.agents.registry")


@dataclass
class AgentInfo:
    """Metadata for a running sub-agent."""

    task_id: str
    session: ClaudeSession
    mode: str  # "plan" or "execute"
    started_at: float = field(default_factory=time.monotonic)
    _task: asyncio.Task[ClaudeResult] | None = field(default=None, repr=False)
    _result: ClaudeResult | None = field(default=None, repr=False)

    @property
    def elapsed_seconds(self) -> float:
        return time.monotonic() - self.started_at

    @property
    def is_running(self) -> bool:
        return self._task is not None and not self._task.done()

    @property
    def result(self) -> ClaudeResult | None:
        if self._task is not None and self._task.done():
            with contextlib.suppress(Exception):
                self._result = self._task.result()
        return self._result

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "mode": self.mode,
            "elapsed_seconds": round(self.elapsed_seconds, 1),
            "is_running": self.is_running,
        }


class AgentRegistry:
    """Tracks running sub-agent processes."""

    def __init__(self) -> None:
        self._agents: dict[str, AgentInfo] = {}

    async def spawn(
        self,
        task_id: str,
        prompt: str,
        *,
        mode: str = "plan",
        cli_command: str = "claude",
        model: str = "sonnet",
    ) -> AgentInfo:
        """Start a new sub-agent for a task.

        Raises ValueError if a sub-agent is already running for this task.
        """
        if task_id in self._agents and self._agents[task_id].is_running:
            raise ValueError(f"Agent already running for task {task_id}")

        permission_mode = "plan" if mode == "plan" else "bypassPermissions"
        session = ClaudeSession(
            prompt,
            cli_command=cli_command,
            model=model,
            permission_mode=permission_mode,
        )

        info = AgentInfo(task_id=task_id, session=session, mode=mode)
        # Run the session as a background asyncio task
        info._task = asyncio.create_task(session.run(), name=f"agent-{task_id}")
        self._agents[task_id] = info

        logger.info("Spawned %s agent for task %s", mode, task_id)
        return info

    async def cancel(self, task_id: str) -> bool:
        """Cancel a running sub-agent. Returns True if it was running."""
        info = self._agents.get(task_id)
        if info is None:
            return False
        if info.is_running:
            await info.session.cancel()
            if info._task is not None:
                info._task.cancel()
            logger.info("Cancelled agent for task %s", task_id)
            return True
        return False

    def get(self, task_id: str) -> AgentInfo | None:
        """Get agent info for a task, or None."""
        return self._agents.get(task_id)

    def list_active(self) -> list[AgentInfo]:
        """Return all currently running agents."""
        return [info for info in self._agents.values() if info.is_running]

    def collect_finished(self) -> list[tuple[str, ClaudeResult]]:
        """Collect results from agents that finished since last call.

        Returns (task_id, result) pairs and removes them from the registry.
        """
        finished: list[tuple[str, ClaudeResult]] = []
        to_remove: list[str] = []

        for task_id, info in self._agents.items():
            if not info.is_running and info._task is not None:
                result = info.result
                if result is not None:
                    finished.append((task_id, result))
                to_remove.append(task_id)

        for task_id in to_remove:
            del self._agents[task_id]
            logger.info("Collected finished agent for task %s", task_id)

        return finished

    def __len__(self) -> int:
        return len(self._agents)
