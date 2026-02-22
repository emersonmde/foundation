# Foundation — Architecture Decisions

## AD-1: `claude -p` subprocess, not Claude Agent SDK

**Decision:** All AI invocations shell out to `claude -p` via `asyncio.create_subprocess_exec`.

**Rationale:** The Claude Agent SDK (Python/TypeScript) requires API key authentication. The Max 20x subscription uses OAuth, which the SDK explicitly does not support for third-party use. `claude -p` authenticates via the CLI's own OAuth flow and bills against the Max plan. This is the only supported path for personal automation on a Max subscription.

**Consequences:**
- Must parse NDJSON stream-json output from subprocess stdout
- Session management via `--resume <session-id>` flag
- No in-process hooks — must use MCP tools for agent-orchestrator communication
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

**Rationale:** The daemon needs to manage Docker containers (create, start, exec, stop, remove). Running inside Docker would require Docker-in-Docker or Docker socket mounting — the exact security anti-pattern we're trying to avoid. A native process has direct, secure access to the Docker API.

**Cross-platform approach:** The daemon's core loop is pure asyncio — no platform-specific code. Process supervision is a deployment concern handled outside the Python code:
- **Development:** `python -m foundation run` as a foreground process (Ctrl+C to stop)
- **macOS early operation:** launchd plist or a simple shell wrapper, if persistence is needed
- **Linux production:** systemd unit file with watchdog, restart-on-failure, journald logging

Self-update restart (Milestone 0.5) should use `os.execv()` to replace the current process, which works on both platforms. systemd restart is an optimization for production, not a hard dependency.

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

## AD-8: MCP tools for agent-orchestrator communication

**Decision:** Inject custom MCP tools into sub-agent sessions via `--mcp-config` for structured communication back to the orchestrator.

**Rationale:** Rather than parsing natural language output to detect agent state, MCP tools provide a typed interface. The agent can call `report_progress()`, `request_decision()`, `submit_plan()` etc.

**Implementation:** A Python MCP server process that communicates with the orchestrator via Unix socket or stdin/stdout. Injected per-session with `--strict-mcp-config` to prevent agents from using any other MCP tools.

## AD-9: Orchestrator meta-reasoning is expensive — use heuristics first

**Decision:** Intervention detection uses heuristic checks (pattern matching, counters, file diff analysis) as the first line. Only escalate to a `claude -p` meta-analysis when heuristics flag something.

**Rationale:** Every `claude -p` call for meta-reasoning burns Max quota. The orchestrator must be selective. Simple checks (same error 3 times, files modified outside plan scope, turn count approaching limit) don't need AI to detect. AI is reserved for "what went wrong and what should we do?" analysis.

## AD-10: Messaging adapter interface

**Decision:** The orchestrator communicates through an abstract messaging interface, not directly through Telegram APIs.

**Rationale:** Enables future addition of Signal, Matrix, ntfy, or custom PWA adapters without changing orchestration logic. The interface defines: `send_message`, `edit_message`, `wait_for_callback`, `wait_for_reply`.

## AD-11: Self-update via systemd restart

**Decision:** Foundation updates itself by pulling from git, reinstalling dependencies, and signaling systemd to restart the service. State is persisted to SQLite before restart and restored on startup.

**Rationale:** The MVP must be self-bootstrapping — Foundation develops its own next version. This requires a reliable update/restart cycle. systemd provides process supervision, restart-on-failure, and watchdog support. Persisting state to SQLite (not in-memory) means restarts are seamless.

**Rollback:** If the new version fails its health check (Telegram connectivity, basic `claude -p` invocation), the service automatically reverts to the previous git commit and restarts.

## AD-12: Usage pacing separates orchestrator from coding agents

**Decision:** Token budget enforcement applies only to coding agent `claude -p` sessions. The orchestrator's own `claude -p` calls (to understand human messages and generate responses) are exempt from pacing limits.

**Rationale:** The human must never feel like the system is unresponsive. The orchestrator's Telegram response cost is small (a single short `claude -p` call) compared to coding sessions (many turns, tool use, large context). Pacing the coding work while keeping the orchestrator responsive is the right trade-off.

**Implementation:** Track two usage categories separately — "orchestrator" (exempt from pacing) and "coding" (subject to daily/weekly budget). Both count against the actual Max quota, but only coding usage is throttled.

## AD-13: MVP runs unsandboxed on Foundation's own repo

**Decision:** Milestone 0 (the self-bootstrapping MVP) runs coding agents directly on the host, without Docker sandboxing. Agents operate only on Foundation's own repository.

**Rationale:** The MVP needs to work on the first shot. Docker sandboxing adds significant complexity (image building, network setup, Squid proxy, volume mounts). Since the MVP only operates on its own codebase, the blast radius is already limited — the worst case is a broken Foundation that gets rolled back. Sandboxing is added in Milestone 1 before any external repos are touched.

**Risk acceptance:** An agent could damage the host system. Mitigated by: (1) the repo is git-tracked so changes are reversible, (2) `--permission-mode plan` for planning prevents writes, (3) the human approves all plans before execution.
