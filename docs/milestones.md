# Foundation — Development Milestones

## Path to Self-Bootstrapping MVP

The MVP is the point where Foundation can develop itself. Everything after the MVP is developed BY Foundation, not by hand. The path is broken into small milestones to get there as fast as possible.

---

### Milestone 0.1: Core Infrastructure ✅

The plumbing everything else depends on.

**Status:** Complete (2026-02-22). `tox` passes all environments (format, lint, typecheck, 61 tests, audit).

**Deliverables:**
- Project scaffolding: pyproject.toml, src layout, config loading (TOML)
- Stream-JSON parser: async NDJSON reader that yields typed events (init, assistant, tool_use, tool_result, result)
- Claude CLI wrapper: spawn `claude -p` as subprocess, pipe prompts, collect streaming output, capture session IDs
- Structured logging
- Basic SQLite schema for persistent state (task queue, usage tracking)
- Claude CLI stub for Layer 2 integration tests
- CLI entry points: `health-check`, `dump-state`, `run-task`

**Verification:** Python module can invoke `claude -p` with a prompt, parse the streaming output, and return a structured result. All from a test script, no daemon yet.

### Milestone 0.2: Telegram Interface ✅

The human control surface. Once this works, the human can interact with the system from their phone.

**Status:** Complete (2026-02-22). `tox` passes all environments (format, lint, typecheck, 92 tests, audit).

**Deliverables:**
- Telegram bot: manual lifecycle integration with asyncio loop
- Auth guard: reject all messages not from the configured user ID
- Send/receive text messages
- Inline keyboards for structured responses (approve/reject/modify)
- Callback query handling
- Basic command routing (/status, /help)

**Verification:** Send a message to the bot, get a response. Send a message with an inline keyboard, tap a button, bot acknowledges the selection. Unauthorized users are rejected.

### Milestone 0.3: Main Event Loop + Task Lifecycle ✅

The orchestrator's brain. Receives tasks, plans them, gets approval, executes them.

**Status:** Complete (2026-02-22). `tox` passes all environments (format, lint, typecheck, 108 tests, audit).

**Deliverables:**
- Transport-agnostic `foundation.messaging` package with `MessagingAdapter` ABC and `IncomingMessage` type
- Orchestrator class with two concurrent coroutines (message listener + main loop)
- Task state machine: pending → planning → awaiting_approval → executing → complete/failed/cancelled
- Planning phase: spawn `claude -p --permission-mode plan`, capture plan output
- Plan review: send plan summary via messaging with approve/reject/modify buttons, wait for response
- Modify flow: resume planning session with human feedback, re-send for approval
- Execution phase: resume session with write permissions on approval (runs on host, Foundation repo only)
- Completion detection: parse result event from stream-json, notify via messaging
- Task persistence: save task state to SQLite so it survives restarts
- Startup recovery: reset interrupted tasks on restart (planning→pending, executing→failed)
- Daemon mode: `foundation run` entry point with signal handling for graceful shutdown
- Claude CLI stub `auto` mode: routes fixture by permission-mode for lifecycle testing

**Verification:** Integration tests demonstrate: submit task → plan (stub) → approve → execute (stub) → complete notification. Reject and modify flows also tested. All using StubAdapter + Claude CLI stub + in-memory SQLite.

### Milestone 0.4: Usage Pacing

Prevents Foundation from burning through the weekly quota in a day.

**Deliverables:**
- Token usage tracking: extract input/output token counts from stream-json result events
- Usage ledger: record per-session token usage in SQLite with timestamps
- Daily and weekly budget calculation (configurable targets, default 75%/75%)
- Budget enforcement: before starting a coding session, check remaining budget — if exhausted, pause the work queue
- Scheduled wake-up: when paused for budget, set an asyncio timer to resume in the next work window
- Telegram alerts: notify when budget is low, when agents pause, when they resume
- Orchestrator exemption: `claude -p` calls to handle Telegram messages are never throttled

**Verification:** Configure a low daily budget. Submit several tasks. Foundation works through them until it hits the budget, pauses, notifies via Telegram, and resumes after the configured interval. Human can still interact with Foundation via Telegram while coding agents are paused.

### Milestone 0.5: Self-Update + Restart

The final piece: Foundation can apply its own changes and restart into the new version.

**Deliverables:**
- Self-update trigger: after completing a task on Foundation's own repo, detect that the codebase changed
- Update sequence: `git add/commit` the changes, `pip install -e .` (or equivalent), `os.execv()` to restart the process
- State persistence: before restart, ensure all state is flushed to SQLite (pending tasks, usage ledger, current position in work queue)
- State restoration: on startup, load state from SQLite, resume where it left off
- Health check: after restart, verify Telegram connectivity and basic `claude -p` invocation, report status via Telegram
- Rollback: if health check fails, revert the last commit, reinstall, restart again
- Optional: systemd unit file for Linux production deployment (process supervision, journald integration)
- On-demand reports: /report command triggers a summary of recent progress, challenges, and successes (uses cached state, invokes `claude -p` to format if needed)

**Verification:** Send Foundation a task that modifies its own code. It plans, gets approval, executes, commits, restarts, comes back online, and reports success via Telegram. The change is live in the running version. If a bad change is introduced, it rolls back and reports the failure.

---

**MVP achieved.** After Milestone 0.5, the human can describe features via Telegram and Foundation builds them. The human's role shifts from writing code to reviewing plans and answering product questions.

**What the MVP intentionally defers:**
- Autonomous work planning (orchestrator is reactive — works on what it's told, one task at a time)
- Project concept (MVP operates on Foundation only, no multi-project awareness)
- Docker sandboxing (agents run on host — acceptable because the only project is Foundation itself)
- Code review phase (human reviews via Telegram plan approval)
- Test phase automation (human verifies or orchestrator runs a simple test command)
- Intervention detection heuristics (human monitors via /status and /report)
- Long-lived memory system (use CLAUDE.md files for now)
- PTY proxy
- Multi-task concurrency (one task at a time)
- MCP tools for agent communication (parse output instead)
- Variable autonomy levels (MVP operates at Level 1 — approve everything)
- LLM backend extensibility (Claude CLI only; AD-14 abstraction added when a second backend is needed)

---

## Post-MVP Milestones

Everything below is developed by Foundation itself, with human oversight via Telegram.

### Milestone 1: Project Management + Autonomous Work Planning

The orchestrator becomes a coworker, not a task queue. It manages its own workload across multiple projects, discovers work from project docs, and makes scheduling decisions. See AD-13.

**Deliverables:**
- Project data model: config (repo path, docs location, priority), DB schema (project_id on tasks), registration via config or Telegram
- Work discovery: read project milestones/requirements docs to determine available work units. When one milestone completes, identify what's next without being told.
- Work planner: decide what to work on next based on explicit human priority, available token budget, blocking state, and logical stopping points
- Human directives: "work on X", "also pick up Y", "finish all milestones on Z", "focus on X until I say otherwise" — parsed and stored as project-level guidance
- Project switching: complete a natural unit of work (milestone, meaningful task) before context-switching. Don't abandon work mid-milestone.
- Capacity-aware scheduling: when token budget is limited, distribute progress across projects proportionally rather than exhausting budget on one
- Priority escalation: when priorities are unclear or conflicting, ask the human ("I have X and Y available, which should I focus on?") rather than guessing
- Idle detection: when all projects are blocked, out of documented scope, or need human decisions, pause and notify — don't spin

**Verification:** Register two projects. Tell Foundation to work on both. It reads their milestones, starts on the higher-priority one, completes a milestone, considers whether to continue or switch, and asks the human when priorities are ambiguous. When one project is blocked on approval, it switches to the other. When budget is tight, it makes proportional progress on both.

### Milestone 2: Variable Autonomy

The approval dial. Builds on project management — at L3, the orchestrator auto-approves routine work and keeps going through milestones autonomously.

**Deliverables:**
- Configurable autonomy level (1/2/3) stored in config, changeable via Telegram (globally or per-project)
- Level 1: approve every plan (current MVP behavior)
- Level 2: approve plans, get notified of completions, escalate on problems
- Level 3: auto-approve plans matching learned patterns, only escalate on interventions or ambiguity
- Escalation policy: always escalate product questions, ambiguity, stuck situations, and one-way-door decisions regardless of level
- Autonomy + project management interaction: at L3 with multiple projects, Foundation works through milestones across projects with minimal human involvement, only surfacing strategic decisions

### Milestone 3: Docker Sandbox

Sub-agents run in isolated containers. Prerequisite for working on any repo other than Foundation.

**Deliverables:**
- Docker image for dev containers: Python, Node, Rust toolchains, Claude Code CLI
- Compose template: internal network + Squid proxy for egress filtering
- Container lifecycle management: create, start, exec, stop, remove per task
- Workspace provisioning: clone repo, checkout branch, bind-mount into container
- Branch isolation: each task gets its own git branch
- Container config hardening: cap-drop, read-only root, resource limits, no socket mount
- Squid domain allowlist: configurable per project

### Milestone 4: Full Task Lifecycle

Complete the plan → approve → execute → review → test pipeline.

**Deliverables:**
- Code review phase: spawn a SEPARATE `claude -p` session to review changes (fresh context, unbiased)
- Test phase: run project test suite, parse results, retry on failure (configurable limit)
- Phase transitions: orchestrator decides when to advance, loop back, or escalate
- Checkpoint/rollback: git commit at phase boundaries, ability to reset
- Decision logging: record all human decisions with timestamps and reasoning

### Milestone 5: Intervention Detection

Detect when agents are going off the rails and take corrective action.

**Deliverables:**
- Heuristic monitors: loop detection, scope creep, turn counter, error pattern matching
- AI-powered meta-analysis when heuristics flag issues
- Telegram alerts with action buttons (retry, modify plan, abort, manual takeover)
- Configurable thresholds
- Execution log capture for post-mortem analysis

### Milestone 6: Long-Lived Memory

Structured knowledge that persists across tasks and improves over time.

**Deliverables:**
- Memory file structure: per-project topic files
- Decision history and mistake log
- Context injection: select relevant memory for each task prompt
- Memory updates and pruning after task completion
- Token budget tracking for memory injection

### Milestone 7: Multi-Task Concurrency

Run multiple tasks in parallel. Distinct from project-level scheduling (Milestone 1) — this is about executing N tasks simultaneously.

**Deliverables:**
- Configurable concurrency limit (max simultaneous `claude -p` sessions)
- Model routing: Sonnet for routine work, Opus for planning/reasoning
- Concurrent session management and resource isolation
- Budget distribution across concurrent tasks
- Interaction with work planner: which tasks are safe to parallelize (different projects, independent branches)

### Milestone 8: PTY Proxy (Interactive Command Support)

Let agents interact with long-running processes.

**Deliverables:**
- PTY session manager (tmux/screen inside containers)
- MCP tools: `pty_start`, `pty_send`, `pty_read`, `pty_stop`
- Session persistence across `claude -p` invocations
- Timeout handling

### Milestone 9: Polish + Operational Maturity

Production hardening for daily use.

**Deliverables:**
- Health checks and watchdog (systemd integration on Linux, process-level on macOS)
- Crash recovery: resume interrupted tasks on restart
- Configuration hot-reload
- Error aggregation (batch and summarize, don't spam)
- Autonomy level learning: track approval patterns to suggest upgrades
- Documentation: operations guide, configuration reference
