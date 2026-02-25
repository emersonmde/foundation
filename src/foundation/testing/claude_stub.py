"""Drop-in Claude CLI stub for Layer 2 integration tests.

Usage:
    python -m foundation.testing.claude_stub -p [flags] PROMPT

Controlled by environment variables:
    CLAUDE_STUB_SCENARIO: success | error | rate_limit | timeout | auto (default: success)
    CLAUDE_STUB_SESSION_ID: override session_id in output (default: stub-session-001)
    CLAUDE_STUB_DELAY_MS: simulated latency in ms (default: 0)
    CLAUDE_STUB_FIXTURE_DIR: custom fixture directory (default: built-in fixtures)

When CLAUDE_STUB_SCENARIO=auto, the fixture is selected based on --permission-mode:
    plan → plan-output.jsonl
    bypassPermissions → simple-response.jsonl
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

_SCENARIO_TO_FIXTURE = {
    "success": "simple-response.jsonl",
    "error": "rate-limit-error.jsonl",
    "rate_limit": "rate-limit-error.jsonl",
    "tools": "execution-with-tools.jsonl",
    "plan": "plan-output.jsonl",
    "permission_denied": "permission-denied.jsonl",
    "orchestrator": "orchestrator-iteration.jsonl",
}

_PERMISSION_MODE_TO_FIXTURE = {
    "plan": "plan-output.jsonl",
    "bypassPermissions": "simple-response.jsonl",
}


def _find_fixture_dir() -> Path:
    """Resolve fixture directory — env var or discover relative to package."""
    env_dir = os.environ.get("CLAUDE_STUB_FIXTURE_DIR")
    if env_dir:
        return Path(env_dir)
    return Path(__file__).parents[3] / "tests" / "fixtures" / "stream-json"


def main(argv: list[str] | None = None) -> None:
    """Entry point for the stub CLI."""
    parser = argparse.ArgumentParser(description="Claude CLI stub")
    parser.add_argument("-p", "--print", action="store_true", dest="print_mode")
    parser.add_argument("--output-format", default="stream-json")
    parser.add_argument("--verbose", action="store_true")
    parser.add_argument("--max-turns", type=int, default=0)
    parser.add_argument("--resume", type=str, default=None)
    parser.add_argument("--session-id", type=str, default=None)
    parser.add_argument("--permission-mode", type=str, default="plan")
    parser.add_argument("--model", type=str, default="sonnet")
    parser.add_argument("--mcp-config", type=str, default=None)
    parser.add_argument("--strict-mcp-config", action="store_true")
    parser.add_argument("--allowedTools", type=str, default=None)
    parser.add_argument("--append-system-prompt", type=str, default=None)
    parser.add_argument("prompt", nargs="?", default="")

    args = parser.parse_args(argv)

    scenario = os.environ.get("CLAUDE_STUB_SCENARIO", "success")
    session_id = os.environ.get("CLAUDE_STUB_SESSION_ID", "stub-session-001")
    delay_ms = int(os.environ.get("CLAUDE_STUB_DELAY_MS", "0"))

    if delay_ms > 0:
        time.sleep(delay_ms / 1000.0)

    # Handle timeout scenario — sleep longer than any reasonable test timeout
    if scenario == "timeout":
        print("Stub: timeout scenario — sleeping 300s", file=sys.stderr)
        time.sleep(300)
        return

    fixture_dir = _find_fixture_dir()

    # Auto mode: select fixture based on permission mode
    if scenario == "auto":
        permission_mode = args.permission_mode
        if permission_mode not in _PERMISSION_MODE_TO_FIXTURE:
            print(
                f"Stub warning: unknown permission-mode {permission_mode!r} in auto mode, "
                "falling back to simple-response.jsonl",
                file=sys.stderr,
            )
        fixture_name = _PERMISSION_MODE_TO_FIXTURE.get(permission_mode, "simple-response.jsonl")
    elif scenario not in _SCENARIO_TO_FIXTURE:
        print(
            f"Stub warning: unknown scenario {scenario!r}, falling back to simple-response.jsonl",
            file=sys.stderr,
        )
        fixture_name = "simple-response.jsonl"
    else:
        fixture_name = _SCENARIO_TO_FIXTURE[scenario]

    fixture_path = fixture_dir / fixture_name

    if not fixture_path.exists():
        print(f"Stub error: fixture not found: {fixture_path}", file=sys.stderr)
        sys.exit(1)

    # Read fixture and replace session_id via JSON parsing for correctness
    lines = fixture_path.read_text().splitlines()
    for line in lines:
        try:
            obj = json.loads(line)
            if "session_id" in obj:
                obj["session_id"] = session_id
            print(json.dumps(obj), flush=True)
        except json.JSONDecodeError:
            print(line, flush=True)

    # Exit code based on scenario
    if scenario in ("error", "rate_limit"):
        sys.exit(1)
    sys.exit(0)


if __name__ == "__main__":
    main()
