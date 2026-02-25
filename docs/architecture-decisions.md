# Foundation — Architecture Decisions

## AD-1: `claude -p` subprocess for MVP, Agent SDK as future backend

**Decision:** The MVP uses `claude -p` via `asyncio.create_subprocess_exec`. The Claude Agent SDK (`claude-agent-sdk` Python package) is a planned future backend (see AD-14).

**Rationale:** The TOS situation for programmatic Max plan access is nuanced (as of February 2026):

- **`claude -p` with Max OAuth:** Clearly permitted. The official docs explicitly promote `claude -p` for automation and CI/CD, and it runs the official Claude Code binary. This is "where we otherwise explicitly permit it" under Consumer TOS Section 3.
- **Agent SDK with Max OAuth:** The written legal docs (code.claude.com/docs/en/legal-and-compliance) state that using OAuth tokens with the Agent SDK "is not permitted." However, an Anthropic employee (Thariq Shihipar, who works on Claude Code) publicly contradicted this, stating "Nothing is changing about how you can use the Agent SDK and MAX subscriptions" and that Anthropic wants to "encourage experimentation" (only commercial products should use API keys).
- **Enforcement so far:** Anthropic has only targeted third-party tools that extract OAuth tokens (OpenClaw, OpenCode) via client fingerprinting. No enforcement against people using the official `claude -p` binary or the Agent SDK for personal automation.

The MVP uses `claude -p` because it's unambiguously permitted. The Agent SDK is worth adopting later because it provides proper `canUseTool` callbacks for real-time HITL (see AD-14), which are significantly better than the CLI's `permission_denials` resume loop.

**Consequences of `claude -p` approach:**
- Must parse NDJSON stream-json output from subprocess stdout
- Session management via `--resume <session-id>` flag
- HITL via `permission_denials` parsing → Telegram → resume loop (functional but adds latency)
- Must handle subprocess lifecycle (spawn, monitor, kill, cleanup)
- Cannot nest `claude` inside another `claude` session (CLAUDECODE env var check) — irrelevant since the orchestrator is a standalone Python daemon

## AD-2: Telegram for human interaction

**Decision:** Telegram bot via python-telegram-bot v20+.

**Alternatives considered:**
- Signal: E2E encrypted but no inline buttons, fragile reverse-engineered bridge
- Matrix: Self-hostable but requires running a homeserver (ops overhead)
- ntfy: Good for push notifications but cannot handle free-text conversations
- Custom PWA: Maximum control but significant build effort

**Rationale:** Telegram has the best bot API (inline keyboards, callbacks, rich HTML formatting), zero infrastructure requirements, and a mature async Python library. The messaging layer is designed as a pluggable adapter so alternatives can be added later.

**Trade-off:** Bot messages are not E2E encrypted (server-side encryption only). Acceptable for personal/open-source development. For proprietary code, a Signal or PWA adapter should be added.

## AD-3: Squid proxy on internal Docker network for egress filtering

**Decision:** Each task's container stack includes a Squid forward proxy on a dual-homed network. Dev containers are on an internal-only network with no direct internet access.

**Alternatives considered:**
- `--network none`: Can't install dependencies
- DNS filtering: Trivially bypassed by hardcoding IPs
- IP-based iptables: CDN IP ranges are too broad
- Cilium/Calico: Require host-level installation

**Rationale:** Squid inspects TLS SNI on CONNECT requests for domain filtering without MITM. The internal network means the proxy cannot be bypassed. Domain allowlist is a simple text config.

## AD-4: Hybrid storage — SQLite + markdown files

**Decision:** Use SQLite for structured operational data (task state, decisions, sessions, rate limit tracking). Use markdown files for memory/knowledge (project conventions, architecture notes, mistake logs).

**Rationale:**
- SQLite: Zero config, built into Python, ACID transactions, easy to query for operational data. Single file, no separate service.
- Markdown files: Human-readable, can be injected directly into agent prompts, easy to version control, agents can read them naturally with the Read tool.

**Trade-off:** No semantic search on SQLite. If semantic search is needed later, can add a vector store or use `claude -p` itself to find relevant memory entries.

## AD-5: Native process, not Docker — systemd on Linux, foreground on macOS

**Decision:** The orchestrator daemon runs as a native process, not inside Docker. On Linux (production), this is a systemd service. On macOS (development/early operation), this is a foreground process or launchd agent.

**Rationale:** Two reasons:
1. The daemon needs to manage Docker containers (create, start, exec, stop, remove). Running inside Docker would require Docker-in-Docker or Docker socket mounting — the exact security anti-pattern we're trying to avoid.
2. The orchestrator itself is an AI agent loop (see AD-8, AD-16). Since it runs on the host with no container boundary, its permissions are enforced deterministically via CLI flags (`--allowedTools`, `--strict-mcp-config`), not by a sandbox. This is a deliberate design choice — the orchestrator needs host access for Docker management, but its AI sessions are restricted to safe operations with denials forwarded to the human (see AD-16).

**Cross-platform approach:** The daemon's core loop is pure asyncio — no platform-specific code. Process supervision is a deployment concern handled outside the Python code:
- **Development:** `python -m foundation run` as a foreground process (Ctrl+C to stop)
- **macOS early operation:** launchd plist or a simple shell wrapper, if persistence is needed
- **Linux production:** systemd unit file with watchdog, restart-on-failure, journald logging

Self-update restart (Milestone 0.6) should use `os.execv()` to replace the current process, which works on both platforms. systemd restart is an optimization for production, not a hard dependency.

## AD-6: Container configs are hardcoded constants

**Decision:** Docker container specifications (volumes, capabilities, networks, security options) are constructed programmatically in Python code. No field is ever derived from AI output, user text, or external data.

**Rationale:** Direct lesson from OpenClaw's sandbox config injection vulnerabilities. The container spec is a security boundary — it must not be parameterizable by anything the AI can influence.

**What IS configurable (via Foundation's own config file, not AI):**
- Domain allowlist for Squid proxy
- Resource limits (CPU, memory)
- Base image selection
- Workspace paths

**What is NEVER configurable from external input:**
- Volume mounts
- Capability additions
- Network mode
- Security options
- Privileged mode

## AD-7: Plan-then-resume lifecycle

**Decision:** Planning and execution use the same Claude Code session via `--resume`.

**Rationale:** The agent accumulates context during planning (files read, codebase understanding, analysis). Resuming preserves this context for execution, avoiding redundant codebase exploration. The permission mode can change between invocations.

**Flow:**
1. `claude -p --permission-mode plan --output-format stream-json` → captures session_id
2. Human reviews and approves plan
3. `claude -p --resume <session-id> --dangerously-skip-permissions` (inside sandbox)

## AD-8: MCP tools as the orchestrator and agent interface

**Decision:** MCP tools are the primary interface for both the orchestrator's own AI sessions and sub-agent communication. Custom MCP tools are injected via `--mcp-config` + `--strict-mcp-config`.

**Rationale:** The orchestrator is itself an AI agent loop — Claude running with a well-defined toolkit, making decisions about task management, sub-agent monitoring, work discovery, and human communication. Rather than building a procedural Python state machine that parses Claude's natural language output, MCP tools give Claude structured functions to call. The Python code becomes the "body" (implementing tools, enforcing boundaries, bridging to external systems) while Claude is the "brain" (deciding what to do next).

This also applies to sub-agents: rather than parsing their natural language output to detect state, MCP tools provide a typed interface for structured communication back to the orchestrator.

### Fresh-session loop, not a long-running session

The orchestrator does NOT run as a single long-running Claude session. Long sessions hit context compaction (~167K tokens), which causes real information loss — Claude can forget what it was working on after compaction. Instead, the orchestrator runs a **fresh-session loop**:

```
loop:
  1. Start a fresh claude -p session with:
     - System prompt (SDM role, decision principles)
     - Current state injected (task list from SQLite, recent action log, active sub-agents, budget status)
     - MCP tools available
  2. Claude reasons about what to do next, calls MCP tools (reprioritize, spawn agent, message human, etc.)
  3. Session ends when Claude finishes its current unit of work
  4. Context is cleared — state persists only in SQLite and markdown
  5. Go to 1
```

State lives in SQLite and markdown files, not in the context window. Each iteration starts fresh with just the current state injected. No compaction, no amnesia.

The `wait(duration)` MCP tool controls loop pacing. Claude decides how long to wait based on context:
- Sub-agent actively running, expected to finish soon → `wait(120)` (2 minutes)
- Sub-agent running, will take a while → `wait(600)` (10 minutes)
- All work blocked on human input → `wait(3600)` (1 hour)
- Nothing to do, all projects idle → `wait(3600)` or longer

The Python daemon implements `wait` as an async sleep that wakes early on incoming Telegram messages. This makes the loop event-driven without spinning.

### Orchestrator MCP tools

| Tool | Purpose |
|------|---------|
| `read_memory(topic)` | Load relevant memory/knowledge files |
| `update_memory(topic, content)` | Persist learnings, update project knowledge |
| `create_task(description, ...)` | Add work to the task queue |
| `update_task(id, status, notes)` | Track progress, record outcomes |
| `query_tasks(filter)` | Check sub-agent status, find blocked/available work |
| `send_message(text, buttons)` | Communicate with human via Telegram |
| `request_human_input(question, options)` | Escalate a decision to the human and wait for response |
| `spawn_agent(task_id, config)` | Start a sub-agent for a coding task |
| `cancel_agent(task_id)` | Stop a misbehaving sub-agent |
| `wait(seconds)` | Sleep until duration elapses or a Telegram message arrives |

Each tool is implemented in Python — `update_memory` writes to markdown files, `create_task` writes to SQLite, `send_message` goes through the messaging adapter, `wait` is an async sleep with early wake. The AI can call them but cannot exceed what the implementation allows.

### Sub-agent MCP tools

Injected into coding agent sessions (separate tool set from orchestrator):

| Tool | Purpose |
|------|---------|
| `report_progress(step, status)` | Agent reports what it's doing |
| `request_decision(question, options)` | Agent asks orchestrator for input on a one-way-door decision |
| `submit_plan(plan_json)` | Structured plan submission |

**Implementation:** A Python MCP server process that communicates via stdin/stdout. Separate tool sets for orchestrator sessions and sub-agent sessions. All sessions use `--strict-mcp-config` to prevent agents from using any MCP tools we didn't provide.

**Why MCP tools instead of procedural Python:**
- Claude decides when to check on sub-agents, update memory, reprioritize — not a hardcoded state machine
- Structured function calls are more reliable than parsing natural language output
- Adding new capabilities = adding a new MCP tool, not rewriting control flow
- The orchestrator's system prompt defines the SDM role and decision-making principles; the MCP tools are how it acts on those decisions
- Task prioritization, scheduling, and work management are AI reasoning problems, not state machine problems — far simpler to give Claude the tools than to hardcode the logic in Python

## AD-9: The orchestrator is an agent loop — but sub-agent monitoring uses heuristics first

**Decision:** The orchestrator runs as a fresh-session agent loop (see AD-8), using AI for all its decision-making: task management, work discovery, prioritization, human communication, and sub-agent oversight. However, **sub-agent monitoring** between orchestrator iterations uses lightweight heuristic checks (pattern matching, counters, file diff analysis) run by Python code, before including the results in the next iteration's context for AI analysis.

**Rationale:** The orchestrator's fresh-session loop means each iteration costs tokens (system prompt + injected state + reasoning). Not every monitoring check needs a full iteration. Simple heuristics (same error 3 times, files modified outside plan scope, turn count approaching limit, stagnation detection — no file changes after N iterations) can run in Python between iterations. When heuristics flag something, the finding is included in the next iteration's injected state, and Claude decides what to do about it.

This keeps the loop efficient: Claude wakes up when there's something to decide, not to run routine checks.

## AD-10: Messaging adapter interface

**Decision:** The orchestrator communicates through an abstract messaging interface, not directly through Telegram APIs.

**Rationale:** Enables future addition of Signal, Matrix, ntfy, or custom PWA adapters without changing orchestration logic. The interface defines: `send_message`, `edit_message`, `wait_for_callback`, `wait_for_reply`.

## AD-11: Self-update via systemd restart

**Decision:** Foundation updates itself by pulling from git, reinstalling dependencies, and signaling systemd to restart the service. State is persisted to SQLite before restart and restored on startup.

**Rationale:** The MVP must be self-bootstrapping — Foundation develops its own next version. This requires a reliable update/restart cycle. systemd provides process supervision, restart-on-failure, and watchdog support. Persisting state to SQLite (not in-memory) means restarts are seamless.

**Rollback:** If the new version fails its health check (Telegram connectivity, basic `claude -p` invocation), the service automatically reverts to the previous git commit and restarts.

## AD-12: Usage pacing separates orchestrator from coding agents

**Decision:** Token budget enforcement applies only to coding agent `claude -p` sessions. The orchestrator's own fresh-session loop (see AD-8) is exempt from pacing limits.

**Rationale:** Each orchestrator iteration is a short, focused Claude session — inject current state, reason about what to do next, call a few MCP tools, done. This is cheap per iteration (small context, few turns), but the iterations add up over a day. Still, the orchestrator must remain responsive — the human should never feel like the system is unresponsive, and autonomous work management (AD-13) requires the orchestrator to keep making decisions.

**Implementation:** Track two usage categories separately — "orchestrator" (exempt from pacing) and "coding" (subject to daily/weekly budget). Both count against the actual Max quota, but only coding usage is throttled. The orchestrator should use Sonnet for routine iterations and reserve Opus for complex reasoning (e.g., analyzing a sub-agent failure, researching an issue) to manage its own token footprint. The `wait` tool (AD-8) naturally paces the loop — Claude decides how long to sleep between iterations, so it doesn't burn tokens when nothing is happening.

## AD-13: Autonomous work management, not reactive task execution

**Decision:** The orchestrator manages its own workload like a coworker, not a task queue processor. The human provides overall direction ("work on X", "also pick up Y", "finish all milestones on Z") and the orchestrator autonomously discovers work, makes progress, switches between projects at logical stopping points, and asks for guidance when priorities are unclear or decisions are beyond its scope.

**Rationale:** A reactive system that only works when a Telegram message arrives wastes the Max subscription and requires constant babysitting — the opposite of the SDM model. The human's role is Sr. SDM / Product Owner: they set direction and make one-way-door decisions. The orchestrator handles everything else.

**What the orchestrator decides autonomously:**
- What to work on next within a project (reads milestones, picks up the next one)
- When to switch between projects (at logical stopping points, not mid-milestone)
- How to distribute limited token budget across projects (proportional progress, not all-or-nothing)
- Tactical implementation decisions (approach, error handling, retry strategy)
- When a milestone is done and what comes next

**What gets escalated to the human:**
- Product questions (feature behavior, scope, UX)
- Priority conflicts between projects when no clear winner
- One-way-door architectural decisions
- Whether to continue past documented milestones into uncharted work
- Genuine blockers the orchestrator can't resolve

**Consequence:** The orchestrator needs:
- A "project" concept with its own repo, docs, milestone state, and priority
- A work planner that reads project docs to discover available work
- Priority-aware scheduling that considers token budget as a constraint
- The ability to ask the human targeted questions ("I have X and Y available, which should I focus on?") rather than waiting passively

**Implementation mechanism:** The orchestrator's fresh-session loop (AD-8) drives all of this. Each iteration, Claude receives the current task list, project states, and budget status as injected context. It reads project docs via built-in tools (Read, Glob, Grep), manages tasks via MCP tools (`create_task`, `query_tasks`), communicates priorities via `send_message` and `request_human_input`, and spawns coding work via `spawn_agent`. There is no hardcoded state machine for work planning — Claude reasons about what to do next given the current state.

A typical orchestrator iteration might be: "Sub-agent on project A finished. Check its output → update task status → read project A's milestones → decide next task → spawn new sub-agent → wait(600)." Or: "Human sent a Telegram message reprioritizing. Update task priorities → cancel low-priority sub-agent → spawn high-priority one → message human confirming." Each of these is a short, focused session that ends cleanly.

## AD-14: Extensible LLM backend, starting with Claude CLI

**Decision:** The orchestrator invokes AI agents through an abstract interface. The initial (and only MVP) implementation is `claude -p` via subprocess, but the interface must support future backends: Claude Agent SDK, `codex` CLI, OpenAI SDK, Gemini SDK, `opencode`, or others.

**Rationale:** The AI tooling landscape is evolving rapidly. Today, `claude -p` on a Max subscription is the best option for personal automation. The Claude Agent SDK (`claude-agent-sdk` on PyPI) is the most compelling second backend — it wraps the same CLI binary but provides proper in-process callbacks for HITL.

**Why the Agent SDK matters:** The SDK's `canUseTool` callback is an async function that pauses agent execution until it returns. This enables real-time HITL — when the agent wants to use a tool or calls `AskUserQuestion`, the callback fires, the orchestrator forwards the question to Telegram, waits for the human's response, and returns it. The agent never stops; it just waits. This is significantly better than the CLI backend's `permission_denials` → resume loop, which adds latency and restarts the process.

**HITL must be backend-internal:** The orchestrator and sub-agents must be agnostic about how HITL is handled. Each backend encapsulates its own HITL mechanism:
- **CLI backend:** Parses `permission_denials` from output, surfaces questions to the orchestrator, then resumes the session with `--resume` and the answer injected into the prompt
- **Agent SDK backend:** Uses `canUseTool` callback to pause execution, surfaces questions to the orchestrator, returns the answer directly — no resume needed
- **Orchestrator interface:** Both backends present the same async interface: `plan()` and `execute()` that may yield `EscalationRequest` objects (questions needing human input) and accept answers, regardless of the underlying mechanism

**Interface shape:** An `AgentBackend` ABC that provides:
- `plan(prompt, ...) -> PlanResult` — invoke AI in read-only/planning mode
- `execute(prompt, session_ref, ...) -> ExecutionResult` — invoke AI with write permissions, optionally resuming a prior session
- Escalation handling — both backends surface `AskUserQuestion` and tool approval requests through the same interface, hiding whether this uses resume loops or in-process callbacks
- Structured result types with session references, output text, token usage, and status

**Current implementation:** `ClaudeCliBackend` wrapping `ClaudeSession` — the only backend for Milestone 0.x. The abstraction is introduced when the Agent SDK backend is added. Until then, the orchestrator uses `ClaudeSession` directly, but the design keeps backend-specific code isolated from orchestration logic.

**What we do NOT do:**
- Don't build the abstraction prematurely — introduce it when we add a second backend
- Don't try to normalize wildly different APIs into one interface — allow backend-specific capabilities
- Don't couple the orchestrator to Claude-specific concepts (session IDs, stream-json, permission modes)
- Don't expose HITL mechanism details to the orchestrator — each backend handles its own interaction pattern

## AD-15: MVP runs unsandboxed on Foundation's own repo

**Decision:** Milestone 0 (the self-bootstrapping MVP) runs coding agents directly on the host, without Docker sandboxing. Agents operate only on Foundation's own repository.

**Rationale:** The MVP needs to work on the first shot. Docker sandboxing adds significant complexity (image building, network setup, Squid proxy, volume mounts). Since the MVP only operates on its own codebase, the blast radius is already limited — the worst case is a broken Foundation that gets rolled back. Sandboxing is added in Milestone 3 before any external repos are touched.

**Risk acceptance:** An agent could damage the host system. Mitigated by: (1) the repo is git-tracked so changes are reversible, (2) `--permission-mode plan` for planning prevents writes, (3) the human approves all plans before execution.

## AD-16: Two-tier permission model — deterministic for orchestrator, sandboxed for sub-agents

**Decision:** The orchestrator's Claude sessions (running on the host) and sub-agent sessions (running in Docker) have fundamentally different permission models.

### Tier 1: Orchestrator (host, no container)

The orchestrator is an AI agent loop running on the host (AD-5, AD-8). Since there is no container boundary, its permissions are enforced **deterministically via CLI flags** set by Python code when spawning the process:

```bash
claude -p \
  --allowedTools "Read Glob Grep Bash(git:log) Bash(git:status) Bash(git:diff)" \
  --mcp-config orchestrator-tools.json \
  --strict-mcp-config \
  --output-format stream-json
```

- **`--allowedTools`** restricts which built-in Claude Code tools the orchestrator can use (read-only filesystem access, safe git commands). The specific allowlist is set in Foundation's config, not by the AI.
- **`--strict-mcp-config`** restricts MCP tools to exactly what we provide (memory, tasks, messaging, sub-agent management — see AD-8). The AI cannot use any other MCP tools.
- **Permission denials** — if the orchestrator's Claude tries to use a tool outside the allowlist (e.g., an unanticipated Bash command, a file write), it's denied by Claude Code's own permission system. The denial appears in `permission_denials` output. Python code **deterministically** forwards these to the human via Telegram — no AI decides whether to approve. The human approves or denies via Telegram buttons.
- **The orchestrator's Claude cannot approve its own tool calls.** The approval path is: Claude Code denies → Python parses denial → Python forwards to Telegram → human responds → Python resumes session. The AI is not in this loop.

This is the "security through architecture" principle applied to the orchestrator itself. The orchestrator's Claude could be prompt-injected (via malicious repo content, crafted sub-agent output, manipulated error messages). Even if injection succeeds, the attacker can only use the allowed tools and MCP functions — they cannot write arbitrary files, run arbitrary commands, or escalate privileges on the host.

### Tier 2: Sub-agents (Docker containers)

Sub-agents run inside Docker containers with full sandboxing (AD-3, AD-6). Because the container IS the security boundary:

- Sub-agents can run with `--dangerously-skip-permissions` — the container's filesystem isolation, dropped capabilities, read-only root, and network restrictions contain any damage.
- The orchestrator (using AI reasoning) can decide whether to approve tool calls from sub-agents. Even if the AI's judgment is wrong, the blast radius is limited to a disposable container workspace.
- Prompt injection inside a sub-agent cannot escape the container, access other tasks' data, or affect the host.

### Auth sharing for Docker containers

Sub-agents in Docker need Claude Code credentials. Two approaches:

**Preferred: `CLAUDE_CODE_OAUTH_TOKEN` env var.** Run `claude setup-token` once on the host (produces a 1-year token). Store in `.env`. Pass to containers as an env var along with a minimal config containing `{"hasCompletedOnboarding": true}`.

**Fallback: `CLAUDE_CONFIG_DIR` bind-mount.** Mount a host directory containing credentials read-only into containers. The host maintains auth with automatic token refresh.

Sub-agents should have **separate settings** from the host (e.g., `cleanupPeriodDays: 99999`, no plugins, restricted config). These are baked into the container image or mounted from Foundation's own config directory.

### Why not put the orchestrator in Docker too?

The orchestrator needs to: (1) manage Docker containers via the Docker API, and (2) access the host filesystem to read project repos and manage worktrees. Running it in Docker would require Docker socket mounting or Docker-in-Docker — the exact anti-patterns we're avoiding (AD-5). The deterministic CLI-flag approach provides sufficient security for the orchestrator's host-level access.
