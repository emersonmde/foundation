# Foundation

An autonomous development orchestrator that manages [Claude Code](https://docs.anthropic.com/en/docs/claude-code) sub-agents through the full development lifecycle — planning, execution, code review, testing — and communicates with a human operator via Telegram.

You own product direction, priorities, and strategic decisions. Foundation handles tactical implementation — breaking down work, assigning it to Claude Code agents, monitoring progress, and escalating when it needs your judgment.

## How It Works

1. You describe a task via Telegram (free text, from your phone)
2. Foundation spawns a Claude Code agent in read-only mode to produce a plan
3. The plan is sent to you for approval (inline keyboard buttons: approve / reject / modify)
4. On approval, a Claude Code agent executes the plan with write access
5. A separate agent reviews the changes (fresh context, unbiased)
6. The project test suite runs; failures loop back to execution
7. You get a completion summary

All AI runs through `claude -p` (headless CLI mode), billed against a Max subscription. No API keys, no Agent SDK.

## Architecture

- **Python 3.12+**, asyncio-based daemon
- **Claude Code CLI** (`claude -p`) for all AI — subprocess with stream-json (NDJSON) output parsing
- **Telegram** (python-telegram-bot) for human interaction, locked to a single authorized user ID
- **SQLite** for operational state (tasks, usage tracking, sessions)
- **Docker** sandboxing for agent execution (post-MVP) — filesystem, process, and network isolation
- Cross-platform: macOS (development) and Linux/Fedora (production)

## Security Model

Security through architecture, not prompts. Telling an LLM "don't delete important files" is a suggestion it will ignore under adversarial pressure. Foundation's defenses are structural:

- No web UI — Telegram bot with Telegram's own auth
- Container configs constructed in code, never from AI output
- Docker sandbox: internal network, dropped capabilities, read-only root FS, resource limits
- Docker socket never mounted inside containers
- Complete task isolation: separate container, branch, and workspace per task
- No plugin/extension system

## Status

Early development. See [docs/milestones.md](docs/milestones.md) for the roadmap.

- **Milestone 0.1** (Core Infrastructure): Complete
- **Milestone 0.2** (Telegram Interface): Complete
- **Milestones 0.3–0.5**: In progress toward self-bootstrapping MVP

The MVP is the point where Foundation can develop itself. Everything after the MVP is developed *by* Foundation.

## Setup

Requires Python 3.12+ and a [Claude Code](https://docs.anthropic.com/en/docs/claude-code) Max subscription.

```bash
python3.12 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

Configure `config.toml` with your settings and create a `.env` file with your Telegram bot token:

```
FOUNDATION_TELEGRAM_TOKEN=your-bot-token
```

## Development

Run all checks (format, lint, typecheck, test, audit):

```bash
tox run
```

Run a single check:

```bash
tox run -e test      # unit + integration tests
tox run -e lint       # ruff linter
tox run -e typecheck  # mypy strict
```

92 tests. Smoke tests that burn real tokens are gated behind `--run-smoke`.

## License

[MIT](LICENSE)
