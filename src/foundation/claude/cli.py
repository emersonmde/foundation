"""Claude CLI wrapper — subprocess management, streaming, result aggregation."""

from __future__ import annotations

import asyncio
import logging
import os
import shlex
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass

from foundation.claude.events import (
    ResultEvent,
    StreamEvent,
    SystemInitEvent,
    TokenUsage,
)
from foundation.claude.parser import ParseError, parse_stream

logger = logging.getLogger("foundation.claude.cli")

# Env vars to strip from subprocess environment (AD-1, testing-strategy)
_STRIPPED_ENV_VARS = ("CLAUDECODE", "CLAUDE_CODE_ENTRYPOINT")


@dataclass(frozen=True)
class ClaudeResult:
    """Aggregated result from a Claude CLI invocation."""

    session_id: str
    result_text: str
    status: str
    duration_ms: int
    events: list[StreamEvent]
    usage: TokenUsage
    return_code: int


class ClaudeSession:
    """Manages a single Claude CLI invocation."""

    def __init__(
        self,
        prompt: str,
        *,
        cli_command: str = "claude",
        model: str = "sonnet",
        permission_mode: str = "plan",
        max_turns: int = 0,
        resume_session_id: str | None = None,
        extra_flags: list[str] | None = None,
    ) -> None:
        self.prompt = prompt
        self.cli_command = cli_command
        self.model = model
        self.permission_mode = permission_mode
        self.max_turns = max_turns
        self.resume_session_id = resume_session_id
        self.extra_flags = extra_flags or []
        self._process: asyncio.subprocess.Process | None = None
        self._return_code: int = -1

    def _build_command(self) -> list[str]:
        """Build the command-line argument list."""
        parts = shlex.split(self.cli_command)
        parts.extend(["-p", "--output-format", "stream-json", "--verbose"])
        parts.extend(["--model", self.model])
        parts.extend(["--permission-mode", self.permission_mode])

        if self.max_turns > 0:
            parts.extend(["--max-turns", str(self.max_turns)])

        if self.resume_session_id:
            parts.extend(["--resume", self.resume_session_id])

        parts.extend(self.extra_flags)

        # Prompt is always the last positional argument
        parts.append(self.prompt)

        return parts

    @staticmethod
    def _clean_env() -> dict[str, str]:
        """Return a copy of os.environ with Claude-specific vars stripped."""
        env = dict(os.environ)
        for var in _STRIPPED_ENV_VARS:
            env.pop(var, None)
        return env

    async def _read_lines(self, stdout: asyncio.StreamReader) -> AsyncIterator[str]:
        """Yield lines from subprocess stdout."""
        while True:
            line_bytes = await stdout.readline()
            if not line_bytes:
                break
            yield line_bytes.decode("utf-8", errors="replace")

    async def run(self) -> ClaudeResult:
        """Execute the CLI and return an aggregated result."""
        events = [event async for event in self.run_streaming()]
        return _aggregate_result(events, self._return_code)

    async def run_streaming(self) -> AsyncIterator[StreamEvent]:
        """Execute the CLI and yield events as they arrive."""
        cmd = self._build_command()
        logger.info("Invoking: %s [prompt: %d chars]", " ".join(cmd[:-1]), len(cmd[-1]))

        start = time.monotonic()
        self._process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=None,
            env=self._clean_env(),
        )

        if self._process.stdout is None:
            raise RuntimeError("subprocess stdout is None despite PIPE configuration")
        try:
            async for event in parse_stream(self._read_lines(self._process.stdout)):
                yield event
        except ParseError:
            logger.exception("Parse error during streaming")
            raise
        finally:
            await self._process.wait()
            rc = self._process.returncode
            self._return_code = rc if rc is not None else -1
            elapsed_ms = int((time.monotonic() - start) * 1000)
            logger.info("CLI exited with code %d in %dms", self._return_code, elapsed_ms)

    async def cancel(self) -> None:
        """Terminate the subprocess if running."""
        if self._process and self._process.returncode is None:
            self._process.terminate()
            try:
                await asyncio.wait_for(self._process.wait(), timeout=5.0)
            except TimeoutError:
                self._process.kill()
                await self._process.wait()
            logger.info("CLI process terminated")


def _aggregate_result(events: list[StreamEvent], return_code: int) -> ClaudeResult:
    """Build a ClaudeResult from a list of events."""
    session_id = ""
    result_text = ""
    status = "unknown"
    duration_ms = 0
    usage = TokenUsage()

    for event in events:
        if isinstance(event, SystemInitEvent):
            session_id = event.session_id
        elif isinstance(event, ResultEvent):
            session_id = session_id or event.session_id
            result_text = event.result
            status = event.status
            duration_ms = event.duration_ms
            usage = event.usage

    return ClaudeResult(
        session_id=session_id,
        result_text=result_text,
        status=status,
        duration_ms=duration_ms,
        events=events,
        usage=usage,
        return_code=return_code,
    )


async def invoke(
    prompt: str,
    *,
    cli_command: str = "claude",
    model: str = "sonnet",
    permission_mode: str = "plan",
    max_turns: int = 0,
    extra_flags: list[str] | None = None,
) -> ClaudeResult:
    """One-shot convenience: invoke claude -p and return the result."""
    session = ClaudeSession(
        prompt,
        cli_command=cli_command,
        model=model,
        permission_mode=permission_mode,
        max_turns=max_turns,
        extra_flags=extra_flags,
    )
    return await session.run()


async def resume(
    session_id: str,
    prompt: str,
    *,
    cli_command: str = "claude",
    model: str = "sonnet",
    permission_mode: str = "bypassPermissions",
    max_turns: int = 0,
    extra_flags: list[str] | None = None,
) -> ClaudeResult:
    """One-shot convenience: resume a session and return the result."""
    session = ClaudeSession(
        prompt,
        cli_command=cli_command,
        model=model,
        permission_mode=permission_mode,
        max_turns=max_turns,
        resume_session_id=session_id,
        extra_flags=extra_flags,
    )
    return await session.run()
