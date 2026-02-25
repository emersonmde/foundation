# Foundation — Requirements Specification

## The Problem

When using Claude Code, the human role is supervisory: crafting prompts, reviewing plans, approving/rejecting approaches, monitoring execution, catching when agents go off the rails, and re-prompting. This requires sitting at a laptop with a terminal open the entire time.

Foundation automates the supervisor role. It's an always-on daemon that orchestrates Claude Code sub-agents through the full development lifecycle — planning, execution, code review, testing — and escalates to a human via Telegram only when it needs judgment: UX decisions, ambiguous requirements, approach approval, or intervention when an agent is stuck.

---

## Hard Constraints

### AI invocation starts with `claude -p`, designed for extensibility

The initial (and only MVP) AI invocation method is the Claude Code CLI in headless mode (`claude -p`), billing against the Max 20x subscription via OAuth. We never use the Anthropic API directly or API keys for the MVP.

The Claude Agent SDK (`claude-agent-sdk` on PyPI) is the most compelling second backend. It wraps the same CLI binary but provides proper `canUseTool` callbacks for real-time HITL — when an agent needs approval or asks a question, the callback pauses execution, the orchestrator forwards the question to Telegram, and returns the answer without restarting the process. The TOS situation for Agent SDK + Max OAuth is contradictory (written legal docs prohibit it, but an Anthropic employee publicly stated it's fine for personal use) — acceptable risk for personal automation, worth revisiting as TOS stabilizes. See AD-1 for details.

The architecture must support both backends (and future ones: `codex` CLI, OpenAI SDK, Gemini SDK, etc.) through a common interface. Critically, **HITL must be backend-internal**: each backend encapsulates its own mechanism for handling permission requests and `AskUserQuestion` escalations, presenting the same async interface to the orchestrator. See AD-14.

### Security through architecture, not prompts

The single most important design principle. Telling an LLM "don't delete important files" is a suggestion it will ignore under adversarial pressure or prompt injection. Security boundaries must be architectural — containers, filesystem mounts, network isolation, process-level permissions — things the AI agent literally cannot override regardless of what it's told to do.

### Python 3.12+, cross-platform (macOS + Linux)

asyncio-based daemon. Development and early operation on macOS; long-term deployment on an always-on Fedora home server as a systemd service. Must run correctly on both platforms. Prefer standard library where reasonable. No heavy frameworks. No platform-specific subprocess commands in orchestrator code (agents handle their own platform differences via Claude Code).

### Telegram for human interaction

Telegram provides push notifications, inline keyboard buttons for approvals, rich HTML formatting, and works on every device. The bot is locked to a single authorized Telegram user ID.

---

## Feature Requirements

### 1. Multi-Agent Orchestration

Foundation manages development tasks through a lifecycle of phases:

**Planning:** A sub-agent explores the codebase in read-only mode (`--permission-mode plan`) and produces a structured plan. The orchestrator captures this plan from the stream-json output.

**Plan Review:** The plan is sent to the human via Telegram with approve/reject/modify buttons. The human's decision and reasoning are logged as training data for the system to learn approval patterns.

**Execution:** A sub-agent executes the approved plan with write access. The same session can be resumed (`--resume <session-id>`) or a fresh one started. The orchestrator monitors execution in real-time via stream-json output.

**Code Review:** A SEPARATE fresh sub-agent reviews the changes. The reviewer must not be the same session as the builder — fresh context means unbiased review.

**Testing:** Run the project's test suite. Parse results. On failure, loop back to execution with test output as context, up to a configurable retry limit.

**Key:** The orchestrator itself uses `claude -p` for its own meta-reasoning — deciding whether to intervene, summarizing execution logs, updating memory files. Agents all the way down.

### 2. Intervention Detection

The orchestrator must detect when a sub-agent is going off the rails:

- **Looping:** Agent making similar edits repeatedly, same test failing with same error
- **Scope creep:** Agent modifying files not mentioned in the plan, touching unrelated code
- **Error spirals:** Each fix introduces a new break, cascading failures
- **Token burn:** Approaching max-turns without meaningful progress
- **Confusion signals:** Contradictory edits, rewriting code that was just written, undoing its own changes

When detected: pause execution, run a meta-analysis (fresh `claude -p` asking "what went wrong?"), send Telegram alert with situation summary and options (retry with different approach, modify plan, abort, manual takeover). Log everything.

### 3. Sandboxed Execution

Every sub-agent runs in an isolated Docker container. The blast radius of any single agent is limited to a disposable workspace.

**Must provide:**
- Filesystem isolation: agent can only read/write the project workspace
- Process isolation: agent cannot affect other tasks or the host
- Network restrictions: outbound connections limited to package registries and source control
- Resource limits: CPU, memory, disk caps
- Docker socket NEVER accessible from inside the sandbox

**Must allow:**
- Full development tool access within the workspace: rm, git, npm, pip, cargo, make, etc.
- `--dangerously-skip-permissions` is acceptable inside the sandbox because the sandbox IS the permission boundary

**Branch isolation:** Each task works on its own git branch. Parallel tasks cannot interfere.

**Checkpoint/rollback:** System can roll back to any phase boundary.

### 4. Interactive Command Support (PTY Proxy)

Claude Code cannot handle interactive TTY sessions — it times out or hangs on processes expecting stdin.

Foundation must let sub-agents:
- Start a long-running interactive process (dev server, REPL, CLI tool)
- Send input to it
- Read its output (optionally waiting for a specific pattern like "Server ready on port 3000")
- Stop it when done

These sessions must persist across `claude -p` invocations since each headless call is a separate process. Think tmux/screen as backing mechanism, exposed as simple commands the agent can call via MCP tools.

### 5. Long-Lived Memory

Structured documents (markdown files) that persist across tasks and sessions:

- **Project knowledge:** Architecture, conventions, key files, gotchas per repo
- **Decision history:** What was approved/rejected and WHY — teaches the system human preferences
- **Mistake log:** What went wrong and how it was fixed — prevents repeating errors
- **Learned patterns:** Coding style preferences, naming conventions, architectural preferences

**Context efficiency is critical.** Every token costs quota. The orchestrator injects only what's relevant to the current task. Progressive disclosure: start with minimal context, let the agent request more if needed.

**Memory updates:** After tasks complete, the orchestrator reflects on what was learned and updates memory via `claude -p`.

### 6. Telegram Interface

Primary control surface, designed for quick phone interactions.

**Receive:**
- Plan summaries with approve/reject/modify buttons
- Decision requests with clear context and suggested options
- Intervention alerts with situation summary and action choices
- Task completion summaries
- Error reports with enough context to decide
- Periodic status updates on active work

**Send:**
- New task descriptions (free text)
- Status queries
- Pause/resume/abort commands
- Free-form feedback in reply to any prompt

**UX:** Messages concise (phone screen). Inline keyboards for common actions. Don't spam — batch related updates. Plan review shows summary first, full details on request.

### 7. Usage Pacing and Rate Limit Awareness

Max 20x limits reset every 5 hours with a weekly ceiling. Usage is shared across all Claude interfaces (the human uses Claude Code manually too).

**Pacing strategy — coding agents only:**
- **Weekly target:** Coding agents use no more than 75% of weekly quota over 7 days, reserving 25% for manual Claude Code use and unexpected needs
- **Daily target:** Coding agents use no more than 75% of daily budget each day (weekly target / 7)
- **Pacing enforcement:** Track cumulative usage, compare against the budget curve, throttle or pause coding agents when ahead of pace
- **Graceful degradation:** When approaching budget limits, prefer lower-cost operations (Sonnet over Opus, shorter prompts, skip optional meta-analysis)

**Orchestrator responsiveness is never throttled:**
- The orchestrator consumes zero tokens while idle (no polling, no background AI calls)
- When the human sends a Telegram message, the orchestrator invokes `claude -p` to understand and respond — this is always allowed regardless of budget state
- Only coding/development agent work is subject to pacing limits

**Scheduling:**
- When daily budget is exhausted, coding agents pause and the orchestrator schedules a wake-up for the next work window
- Sleep/wake scheduling via systemd timers or internal async scheduling
- On wake, resume the work queue from where it left off
- The orchestrator itself remains responsive to Telegram during sleep periods

**Rate limit handling:**
- Track usage and estimate remaining capacity
- Use Sonnet for routine work, reserve Opus for planning/reasoning
- Queue work intelligently when approaching limits
- Alert when quota is low
- Handle rate limit errors gracefully with backoff
- Limit concurrent `claude -p` sessions

### 8. Self-Update and Restart

Foundation must be able to update its own code and restart cleanly. This is critical for self-bootstrapping — the MVP must be capable enough that Foundation itself can develop the next version of Foundation.

- Pull latest changes from git (its own repo)
- Run any necessary migrations or dependency updates
- Restart the daemon gracefully (drain active work, save state, exec into new version or signal systemd to restart)
- Verify the new version starts successfully (health check after restart)
- Roll back to previous version if the new version fails to start
- Updates can be triggered by the human via Telegram or initiated by the orchestrator after completing a self-improvement task

### 9. Variable Autonomy Level

The level of human involvement must be configurable and adjustable at runtime:

**Autonomy dial (configurable per-project or globally):**
- **Level 1 — High oversight:** Approve every plan, review every code change, confirm every deployment. For initial testing and untrusted projects.
- **Level 2 — Standard:** Approve plans, get notified of completions, escalate on intervention. The default operating mode.
- **Level 3 — High autonomy:** Auto-approve plans that match learned patterns, only escalate on interventions or ambiguous requirements. For mature projects with established patterns.

**What always requires human input regardless of level:**
- Product decisions (what to build, feature scope, UX choices)
- Directional decisions (priority changes, roadmap adjustments)
- Resolving genuine ambiguity in requirements
- Intervention situations where the orchestrator is stuck
- Any action outside the project scope

**Autonomy level can be changed at runtime via Telegram.**

### 10. Operational Mental Model

Foundation operates as a **Software Development Manager (SDM)** reporting to the human, who acts as **Senior SDM + Product Owner**.

**The human's responsibilities (Sr. SDM + Product Owner):**
- Product direction: what to build, feature priorities, UX decisions
- Strategic technical oversight: major architectural choices
- Unblocking the SDM when it's stuck
- Adjusting resourcing (autonomy level, pacing, priorities)

**The orchestrator's responsibilities (SDM role):**
- Break down work into tasks and assign to sub-agents (the "team")
- Make tactical decisions: implementation approach, error handling strategy, retry logic
- Monitor team progress, detect problems early, course-correct
- Report up: progress, blockers, completions, risks
- Maintain team knowledge (memory files, conventions)

**Autonomous work management (AD-13):**

The orchestrator is NOT a reactive request-response bot that waits to be told each step. It is a coworker who manages their own workload. The human provides overall direction — "work on project X", "also pick up project Y", "finish all milestones on Z" — and the orchestrator figures out what to do next, when to switch context, and when to ask for guidance.

This means the orchestrator must:

- **Discover work autonomously.** When assigned a project, read its docs (milestones, requirements, CLAUDE.md) to understand what work exists. When one milestone completes, look for what's next. If the human says "work on this until done," keep going through milestones without being told each one.

- **Manage priorities across projects.** When working on multiple projects, decide which one to work on next based on: explicit human priority, available token budget, whether a project is blocked, and logical stopping points. If priorities are unclear, ask — don't guess. A simple "I have X and Y available, which should I focus on?" is exactly right.

- **Find logical stopping points.** Don't abandon a project mid-milestone to switch. Complete a natural unit of work (a milestone, a meaningful task) before context-switching. If token budget is limited, distribute progress across projects proportionally rather than burning the whole budget on one.

- **Respect capacity constraints.** Token budget isn't just a throttle — it's a scheduling input. If budget is tight and two projects need work, make meaningful progress on both rather than exhausting budget on one. If budget allows, stay focused on the higher-priority project until a stopping point.

- **Ask when uncertain.** The human wants to be consulted on one-way-door decisions, unclear priorities, and product questions — not on tactical implementation choices. "Should I use approach A or B?" is fine to decide autonomously. "Should we build feature X at all?" must be escalated.

**What the orchestrator decides autonomously (tactical):**
- Which milestone/task to work on next within a project
- When to switch between projects (at logical stopping points)
- Implementation approach, error handling, retry strategy
- When to continue vs. pause for budget reasons

**What gets escalated to the human (strategic / one-way doors):**
- Product questions: feature behavior, UX choices, scope decisions
- Ambiguous requirements that need product judgment
- Priority conflicts between projects when no clear winner
- Significant architectural decisions with long-term implications
- Situations where the team is stuck and the SDM can't unblock them
- Budget/resource concerns (quota running low)
- Whether to continue past documented milestones into uncharted work

**On-demand reporting via Telegram:**
- Progress report: what's been done, what's in flight, what's next
- Challenges report: current blockers, recent failures, areas of concern
- Success report: completed tasks, metrics, improvements

### 11. Human Interaction Priority

**The main event loop must always prioritize human interaction over agent management.**

- Incoming Telegram messages from the human are processed immediately, never queued behind agent work
- If the human sends a message while agents are running, the orchestrator responds first, then returns to agent management
- Status queries get instant responses (from cached state, not by spawning AI)
- The human should never feel like they're waiting for an agent to finish before the orchestrator responds to them
- The orchestrator remains responsive even when coding agents are paused due to budget limits

---

## Security Model

### Lessons from OpenClaw

OpenClaw (100k+ GitHub stars, early 2026) is an autonomous AI agent platform whose security failures are instructive.

**CVE-2026-25253 (CVSS 8.8):** One-click RCE via auth token exfiltration. The Control UI trusted a `gatewayUrl` parameter from query strings, auto-connected via WebSocket, and leaked auth tokens. Attackers could disable sandboxing via API, escape Docker, and achieve full host RCE. 17,500+ exposed instances found across 52 countries.

**Docker sandbox config injection:** Malicious configs could mount host directories, Docker socket, or disable seccomp/AppArmor, completely defeating containerization.

**Prompt-based security:** "Don't access sensitive files" as the security model. Prompt injection overrides this trivially.

**Shared context across sessions:** Secrets loaded for one user visible to others.

**Skills marketplace as attack vector:** Community-contributed code executing with agent privileges. Cisco found data exfiltration in third-party skills.

### Foundation's Security Architecture

| Attack Vector | OpenClaw Failure | Foundation's Defense |
|---|---|---|
| Web UI token leakage | Auth tokens in URL params | No web UI — Telegram bot with Telegram's auth |
| Config injection | AI/external input controlled container config | Container configs constructed programmatically in code, never from AI input |
| Prompt-based security | "Don't access files" in prompt | Architectural sandbox: internal Docker network, dropped capabilities, read-only root FS |
| Docker socket exposure | Socket accessible from containers | Never mounted, period |
| Shared context | Cross-session secret leakage | Complete task isolation: separate container, branch, workspace per task |
| Plugin/skill system | Third-party code with agent privileges | No plugin system — scope limited to development orchestration |
| Container escape | Privilege escalation | `--cap-drop=ALL`, `--security-opt=no-new-privileges`, resource limits, Squid egress proxy |

### Core Principle

Every piece of text an LLM processes is a potential prompt injection vector — code from repos, test output, error messages, dependency descriptions. The sandbox means that even if prompt injection succeeds and the agent does something malicious, damage is contained to a disposable workspace.
