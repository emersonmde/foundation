# Foundation — Testing Strategy

## Key Constraint: Claude Code Nesting

Claude Code sets `CLAUDECODE=1` in its environment. Child processes inherit this, and `claude -p` refuses to run when it detects this variable ("cannot be launched inside another Claude Code session").

**Workaround:** Strip `CLAUDECODE` and `CLAUDE_CODE_ENTRYPOINT` from the child process environment. Verified working — a Python subprocess can invoke `claude -p` successfully this way.

**Production irrelevance:** In production, Foundation runs as a systemd service where these env vars don't exist. The workaround is only needed during development when Claude Code is running Foundation's test suite.

**Implementation:** The CLI wrapper should always strip these env vars from the subprocess environment. This is correct behavior regardless — Foundation is not a Claude Code session, so it should never propagate these vars.

## Key Constraint: Interactive Processes

Claude Code cannot handle interactive/long-running processes that don't support piped I/O (stdin/stdout). The Telegram bot's polling loop, the main asyncio event loop, and the systemd daemon itself are all long-running interactive processes.

**Solution:** Foundation must support a non-interactive mode for testing, similar to how `claude -p` (print mode) is the non-interactive counterpart to interactive `claude`.

## Testing Layers

### Layer 1: Unit Tests (pytest, no AI, no network)

Pure logic testing. Fast, cheap, runs in CI or from Claude Code.

**What's tested:**
- Stream-JSON parser: feed it recorded NDJSON, verify it yields correct typed events
- Task state machine: verify transitions, reject invalid states
- Usage pacing: budget calculations, threshold enforcement, scheduling logic
- Config loading: valid/invalid TOML parsing
- Self-update: state serialization/deserialization, version comparison
- Telegram message formatting: plan summaries, reports, truncation

**How:**
- Recorded fixtures: capture real `claude -p` stream-json output into fixture files. Parser tests replay these.
- No mocks of core logic — test the real code with canned inputs.

### Layer 2: Integration Tests (stubbed Claude CLI, stubbed Telegram)

Test component interactions without burning tokens or needing network access.

**Claude CLI stub:**
A simple shell script or Python script that mimics `claude -p` behavior:
- Reads the prompt from arguments
- Writes canned stream-json events to stdout (init, assistant, result)
- Supports `--output-format stream-json`, `--output-format json`, `--max-turns`, `--resume`
- Configurable: different fixtures for different test scenarios (success, error, rate limit, long output)
- Set via config: `cli_command = "python -m foundation.testing.claude_stub"` instead of `"claude"`

**Telegram stub:**
A mock implementation of the messaging adapter interface that:
- Captures sent messages and inline keyboards
- Allows tests to programmatically "send" messages and "press" buttons
- Verifies message ordering, content, and keyboard layout
- No network, no Telegram API

**What's tested:**
- Full task lifecycle: submit task → plan → approve → execute → complete
- Human interaction priority: send a message during execution, verify it's handled immediately
- Usage pacing: run multiple tasks, verify budget enforcement and pause/resume
- Error handling: simulate CLI failures, verify error reporting
- State persistence: run a task, "restart" (reload from SQLite), verify state is restored

**How to run from Claude Code:**
```
pytest tests/integration/ -x
```
All integration tests use the stubs. No real Claude CLI, no real Telegram. Claude Code can run these directly.

### Layer 3: Smoke Tests (real Claude CLI, stubbed Telegram)

Verify Foundation actually works with the real `claude -p`. Burns tokens, so run sparingly.

**What's tested:**
- CLI wrapper correctly parses real stream-json output
- Session IDs are captured and can be used with `--resume`
- Token usage numbers are extracted correctly from result events
- A real plan-then-execute cycle works end-to-end

**How to run:**
```
pytest tests/smoke/ -x --run-smoke
```
Requires `--run-smoke` flag to prevent accidental token burn. Uses the real `claude` CLI but still uses the Telegram stub.

### Layer 4: End-to-End Tests (real Claude CLI, real Telegram)

Full system test. Run manually by the human, not by Claude Code.

**What's tested:**
- Send a task via Telegram on a real phone
- Watch it plan, approve it, watch it execute, see the completion notification
- Verify /status, /report commands work
- Verify self-update cycle

**How:**
- Human runs `python -m foundation` (or `systemctl start foundation`) and interacts via Telegram
- This is the milestone verification step, not an automated test

## Non-Interactive Test Runner

Foundation needs a CLI entry point for testing that doesn't start the Telegram polling loop or the daemon event loop:

```
# Run a single task non-interactively (auto-approve, no Telegram)
python -m foundation run-task --auto-approve "Add a docstring to config.py"

# Check system health (can invoke claude -p, config is valid, SQLite is accessible)
python -m foundation health-check

# Dump current state (tasks, usage, queue)
python -m foundation dump-state
```

These are analogous to `claude -p` — non-interactive counterparts to the daemon mode. They share all the same internal code but skip the Telegram loop and asyncio event loop.

## Claude CLI Stub Specification

The stub must be a drop-in replacement for `claude` that supports:

```
claude_stub -p [--output-format json|stream-json] [--verbose] [--max-turns N] [--resume SESSION_ID] [--permission-mode MODE] PROMPT
```

**Behavior:**
1. Parse arguments
2. Look up a fixture based on the prompt content (hash or keyword match) or use a default
3. Output the fixture data in the requested format (json or stream-json)
4. Exit with the appropriate return code

**Fixture format (stream-json):**
```jsonl
{"type":"system","subtype":"init","session_id":"test-session-001","model":"claude-sonnet-4-20250514",...}
{"type":"assistant","message":{"content":[{"type":"text","text":"Here is my plan..."}],"usage":{"input_tokens":100,"output_tokens":50},...}}
{"type":"result","subtype":"success","session_id":"test-session-001","result":"Plan complete.","usage":{"input_tokens":100,"output_tokens":50},...}
```

**Configurable scenarios:**
- `CLAUDE_STUB_SCENARIO=success` — normal completion
- `CLAUDE_STUB_SCENARIO=rate_limit` — 429 error after init event
- `CLAUDE_STUB_SCENARIO=timeout` — hangs (for testing timeout handling)
- `CLAUDE_STUB_SCENARIO=error` — non-zero exit with error message
- `CLAUDE_STUB_SCENARIO=long_output` — many assistant events (for testing streaming)
- `CLAUDE_STUB_FIXTURE_DIR=/path/to/fixtures` — directory of custom fixture files

## Test Fixtures

Captured from real `claude -p` sessions and stored in `tests/fixtures/`:

```
tests/fixtures/
├── stream-json/
│   ├── simple-response.jsonl       # Single-turn text response
│   ├── plan-output.jsonl           # Planning mode output with structured plan
│   ├── execution-with-tools.jsonl  # Multi-turn with tool_use and tool_result events
│   ├── rate-limit-error.jsonl      # 429 rate limit response
│   └── large-session.jsonl         # Many turns (for testing streaming performance)
└── json/
    ├── simple-response.json
    ├── plan-output.json
    └── error-response.json
```

## What Gets Tested When

| Change type | Layer 1 (unit) | Layer 2 (integration) | Layer 3 (smoke) | Layer 4 (e2e) |
|---|---|---|---|---|
| Parser logic | Always | — | — | — |
| State machine | Always | Always | — | — |
| CLI wrapper | Always | Always | On milestone completion | — |
| Task lifecycle | — | Always | On milestone completion | — |
| Usage pacing | Always | Always | — | — |
| Telegram formatting | Always | Always | — | On milestone completion |
| Self-update | — | Always (simulated) | — | On milestone completion |
| New milestone feature | Always | Always | Yes | Yes |
