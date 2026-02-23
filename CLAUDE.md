# Foundation — Project Instructions

## What This Is

Foundation is an autonomous development orchestrator — an always-on daemon that manages Claude Code sub-agents through the full development lifecycle (planning, execution, review, testing) and communicates with the human via Telegram.

The orchestrator is an SDM (Software Development Manager). The human is the Sr. SDM + Product Owner — they own product direction, priorities, and strategic decisions. The orchestrator makes tactical implementation decisions and escalates when stuck, ambiguous, or facing product questions.

## Architecture

- **Python 3.12+**, asyncio-based daemon. Development on macOS, production on Linux (Fedora). Must run on both.
- **All AI through `claude -p`** — headless CLI mode, billed against Max 20x subscription. No API keys, no Agent SDK.
- **Telegram** for human interaction (python-telegram-bot v20+)
- **SQLite** for operational state (tasks, usage, sessions). **Markdown files** for knowledge/memory.
- **Security through architecture, not prompts** — Docker sandboxing, network isolation, hardcoded container configs

## Key Documentation

Read these before making changes:

- `docs/requirements.md` — Feature requirements and hard constraints
- `docs/architecture-decisions.md` — AD-1 through AD-15, rationale for major decisions
- `docs/milestones.md` — Development roadmap (Milestone 0.1–0.5 = MVP, then post-MVP)
- `docs/testing-strategy.md` — 4-layer testing approach, Claude CLI stub, fixtures
- `docs/research-cli.md` — `claude -p` flags, stream-json format, session management
- `docs/research-telegram.md` — Telegram bot integration patterns
- `docs/research-sandbox.md` — Docker sandbox architecture with Squid proxy
- `docs/research-security.md` — OpenClaw security lessons

## Development Rules

### Claude CLI Wrapper
- Always strip `CLAUDECODE` and `CLAUDE_CODE_ENTRYPOINT` from subprocess environment when invoking `claude -p`. This prevents the nesting check from blocking invocations during development.
- Parse stream-json (NDJSON) output. Requires `--verbose` flag with `--output-format stream-json`.
- Track token usage from result events for budget enforcement.

### Testing
- All new code must have unit tests (Layer 1) and integration tests (Layer 2) using the Claude CLI stub and Telegram stub.
- Never burn real tokens in automated tests without the `--run-smoke` flag.
- Use recorded fixtures in `tests/fixtures/` for parser and integration tests.
- Non-interactive CLI entry points (`run-task`, `health-check`, `dump-state`) for testing without the daemon loop.

### Code Style
- Standard library where reasonable. No heavy frameworks.
- asyncio for concurrency. No threads unless interfacing with blocking libraries.
- Type hints on public interfaces.
- Keep it simple — this project needs to work on the first shot for self-bootstrapping.

### Cross-Platform
- Must run on macOS (development) and Linux (production). No platform-specific code in the orchestrator.
- Never shell out to BSD/GNU utilities (sed, find, xargs, etc.) — use Python stdlib instead.
- No systemd dependency in application code. Process supervision is a deployment concern handled externally.
- Self-update restart uses `os.execv()` (works on both platforms), not systemd-specific mechanisms.

### What NOT to Do
- For the MVP, never use the Anthropic API or API keys — everything goes through `claude -p` (see AD-14 for future extensibility)
- Never derive Docker container configs from AI output or external data (AD-6)
- Never mount the Docker socket inside containers
- Don't over-engineer — the MVP defers Docker, code review, intervention detection, memory, PTY, concurrency, and MCP tools

## Dev Environment

- **Venv:** `.venv` created with `python3.12 -m venv .venv` (system Python is conda 3.9, do not use it)
- **Run all checks:** `.venv/bin/tox run` (format, lint, typecheck, test, audit)
- **Run one check:** `.venv/bin/tox run -e test` (or `format`, `lint`, `typecheck`, `audit`)
- **Secrets:** `.env` file (gitignored) for `FOUNDATION_TELEGRAM_TOKEN`. Never commit secrets.

## Current State

MVP development in progress. See `docs/milestones.md` for current milestone and what's been completed.
