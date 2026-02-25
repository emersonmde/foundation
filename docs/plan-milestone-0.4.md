# Milestone 0.4 Plan: Orchestrator Agent Loop

## Summary

Rearchitect the orchestrator from a procedural state machine (Milestone 0.3) to a fresh-session agent loop with MCP tools (AD-8). The current hardcoded plan-approve-execute flow becomes one of many actions Claude can take via structured MCP tool calls. Python becomes the "body" (implementing tools, enforcing boundaries, bridging to external systems) while Claude is the "brain" (deciding what to do next).

### LangGraph Evaluation

**Verdict: Do not adopt.** LangGraph's graph model puts Python in control of execution flow with the LLM as a node — the exact opposite of Foundation's architecture where Claude is the decision-maker and Python implements the tools. Adopting LangGraph would reintroduce the procedural state machine this milestone is designed to eliminate. It would also add heavy dependencies (`langchain-core`, `pydantic`, `langsmith`, etc.) that contradict the "standard library where reasonable" principle. The fresh-session loop with MCP tools is simpler and more aligned with the architecture.

---

## Scope: What Changes

### What gets replaced

The entire `Orchestrator` class (375 lines) is replaced. The current design has:
- A procedural `_run_task_lifecycle` method with hardcoded phase transitions
- A `_main_loop` that picks pending tasks and drives them through phases
- A `_message_listener` that creates tasks from incoming messages
- Direct `ClaudeSession` calls from Python code for planning/execution

### What stays

- **Claude CLI wrapper** (`foundation.claude.cli`): `ClaudeSession`, `ClaudeResult`, stream-json parsing — all stay. The session wrapper gains new flags (`--mcp-config`, `--strict-mcp-config`, `--allowedTools`, `--append-system-prompt`) but the core subprocess management is unchanged.
- **Database layer** (`foundation.db`): Schema is extended (not replaced). Task CRUD and usage tracking stay.
- **Messaging layer** (`foundation.messaging`, `foundation.telegram`): Adapter ABC, Telegram bot, handlers — all stay. The MCP tools call through the existing `MessagingAdapter`.
- **Testing infrastructure** (`foundation.testing`): Claude stub and Telegram stub stay, but are extended for MCP scenarios.

### What's new

1. **MCP server** — Python process implementing orchestrator tools via stdin/stdout
2. **Fresh-session loop** — replaces the procedural orchestrator
3. **State injection** — builds context snapshots from SQLite/markdown for each iteration
4. **Orchestrator system prompt** — SDM role, decision principles, tool documentation
5. **Sub-agent management** — spawn/cancel/monitor coding agents
6. **Memory system** (basic) — read/write markdown files via MCP tools
7. **Permission denial forwarding** — deterministic Telegram forwarding for denied tool calls
8. **Stub MCP server** — for integration testing

---

## Implementation Plan

### Phase 1: MCP Server Foundation

Build the Python MCP server that the orchestrator's `claude -p` sessions will use. This is the core new component — everything else depends on it.

#### 1.1 MCP Server Process (`foundation/mcp/server.py`)

A Python script that implements the [Model Context Protocol](https://modelcontextprotocol.io) stdio transport:
- Reads JSON-RPC requests from stdin
- Dispatches to tool handlers
- Writes JSON-RPC responses to stdout
- Handles `initialize`, `tools/list`, and `tools/call` methods

The server receives configuration via environment variables (database path, messaging socket path, etc.) set by the parent Python daemon when spawning the `claude -p` process.

**Communication bridge:** The MCP server runs as a child of `claude -p`, which is itself a child of the Foundation daemon. The MCP server needs access to the daemon's state (SQLite database, messaging adapter, sub-agent registry). Two approaches:

- **Option A (simpler, MVP):** The MCP server opens its own `aiosqlite` connection to the same database file. SQLite WAL mode supports concurrent readers/writers. For messaging and sub-agent operations, the MCP server communicates with the daemon via a Unix domain socket or a simple TCP socket on localhost.
- **Option B (future):** Use the Agent SDK backend where tools run in-process, eliminating the IPC bridge entirely.

**Recommendation:** Option A for MVP. The IPC bridge is a small socket server in the daemon that accepts commands like `{"method": "send_message", "params": {"text": "...", "buttons": [...]}}` and returns results. This keeps the MCP server stateless and the daemon as the single source of truth for messaging and sub-agent state.

#### 1.2 MCP Tool Definitions

Each tool is a handler function. The MCP server exposes these via `tools/list`:

| Tool | Input Schema | Implementation |
|------|-------------|----------------|
| `create_task(title, description)` | `{title: str, description: str}` | INSERT into tasks table via SQLite |
| `update_task(task_id, status, notes?)` | `{task_id: str, status: str, notes?: str}` | UPDATE tasks table |
| `query_tasks(status?, limit?)` | `{status?: str, limit?: int}` | SELECT from tasks table, return JSON |
| `send_message(text, buttons?)` | `{text: str, buttons?: [{text, data}]}` | IPC to daemon → MessagingAdapter.send_message |
| `request_human_input(question, options)` | `{question: str, options: [{text, data}]}` | IPC to daemon → send with buttons + wait_for_callback |
| `spawn_agent(task_id, prompt, mode?)` | `{task_id: str, prompt: str, mode?: str}` | IPC to daemon → start sub-agent subprocess |
| `cancel_agent(task_id)` | `{task_id: str}` | IPC to daemon → terminate sub-agent |
| `read_memory(topic)` | `{topic: str}` | Read from `memory/<topic>.md` file |
| `update_memory(topic, content)` | `{topic: str, content: str}` | Write to `memory/<topic>.md` file |
| `wait(seconds)` | `{seconds: int}` | IPC to daemon → async sleep with early wake |

**`request_human_input` semantics:** This is a blocking MCP tool call. The MCP server sends the question to the daemon via IPC, the daemon forwards to Telegram with inline buttons, the daemon waits for the callback, and returns the human's answer. The `claude -p` session is suspended waiting for the tool result. This works because MCP tool calls are synchronous from the LLM's perspective — Claude waits for the tool result before continuing.

**`wait` semantics:** Also blocking. The MCP server sends a wait request to the daemon, the daemon sleeps for the specified duration (or wakes early on an incoming Telegram message), and returns a result indicating whether it was woken early (and if so, includes the message). When `wait` returns with an early wake, Claude sees the incoming message in the tool result and can decide what to do about it.

#### 1.3 IPC Bridge (`foundation/mcp/bridge.py`)

A lightweight async socket server running inside the Foundation daemon:
- Listens on a Unix domain socket (path passed to MCP server via env var)
- Accepts JSON-RPC-style requests from the MCP server
- Dispatches to internal daemon methods (messaging, sub-agent management, wait)
- Returns JSON responses

This is intentionally simple — not a full RPC framework, just a thin dispatcher. The bridge methods:
- `send_message(text, buttons)` → calls `MessagingAdapter.send_message`
- `edit_message(message_id, text, buttons)` → calls `MessagingAdapter.edit_message`
- `request_human_input(question, options)` → sends message with buttons, waits for callback, returns answer
- `spawn_agent(task_id, prompt, mode)` → starts a `ClaudeSession` subprocess, registers it
- `cancel_agent(task_id)` → terminates the subprocess
- `query_agents()` → returns status of active sub-agents
- `wait(seconds)` → async sleep, wakes early on Telegram message, returns wake reason

#### 1.4 MCP Config Generation

Generate the `--mcp-config` JSON that tells `claude -p` how to find the MCP server:

```json
{
  "mcpServers": {
    "foundation": {
      "command": "python",
      "args": ["-m", "foundation.mcp.server"],
      "env": {
        "FOUNDATION_DB_PATH": "/path/to/foundation.db",
        "FOUNDATION_IPC_SOCKET": "/tmp/foundation-ipc.sock",
        "FOUNDATION_MEMORY_DIR": "/path/to/memory/"
      }
    }
  }
}
```

Written to a temp file before each `claude -p` invocation. Cleaned up after.

---

### Phase 2: Fresh-Session Loop

Replace the procedural orchestrator with the AI-driven loop.

#### 2.1 State Snapshot Builder (`foundation/orchestrator.py` — new implementation)

A function that gathers current state from all sources and formats it for injection:

```python
async def build_state_snapshot(db, agent_registry, budget_tracker=None) -> str:
    """Build a text summary of current state for the orchestrator's context."""
```

The snapshot includes:
- **Task list:** All non-terminal tasks with status, age, and last update
- **Active sub-agents:** Running `claude -p` processes with task ID, elapsed time, last known status
- **Recent action log:** Last N actions taken by previous iterations (from an `action_log` table)
- **Pending human messages:** Any Telegram messages received since last iteration
- **Budget status:** Token usage today/this week vs. limits (placeholder until Milestone 0.5)

Format: Structured text (not JSON) that Claude can read naturally. Example:

```
## Current State

### Tasks
- [executing] "Add retry logic to CLI wrapper" (task 3a7f...) — sub-agent running, 4 min elapsed
- [pending] "Update README with new config options" (task 9bc2...)
- [awaiting_approval] "Refactor parser module" (task 1de4...) — plan sent, awaiting human response

### Active Sub-Agents
- task 3a7f: claude -p running (pid 12345), started 4m ago, no errors detected

### Recent Actions (last iteration)
- Spawned sub-agent for task 3a7f
- Sent status update to human
- Called wait(300)

### Pending Messages
- Human sent: "How's the retry logic task going?"
```

#### 2.2 Orchestrator System Prompt (`foundation/prompts/orchestrator.md`)

The system prompt defines the SDM role and behavior:

```markdown
You are Foundation's orchestrator — an autonomous Software Development Manager (SDM).

## Your Role
You manage development tasks by spawning Claude Code sub-agents, monitoring their progress,
communicating with the human (your Sr. SDM / Product Owner) via Telegram, and making
tactical implementation decisions.

## Decision Principles
- Act on what needs attention NOW, then wait
- One unit of work per iteration — don't try to do everything at once
- Escalate product questions and one-way-door decisions to the human
- Make tactical decisions (implementation approach, retry strategy) autonomously
- When priorities are unclear, ask the human

## Available Tools
[auto-generated from MCP tool list]

## Typical Iterations
- "Sub-agent finished task X" → update_task(complete) → check for next pending task → spawn_agent or wait
- "Human sent a message" → read it from pending messages → respond via send_message → take action if needed
- "Nothing happening" → wait(3600)
- "New task received" → spawn_agent in plan mode → wait(120)
- "Plan ready for review" → send plan to human via send_message → request_human_input for approval → act on response
```

#### 2.3 New Orchestrator Loop (`foundation/orchestrator.py`)

The new `Orchestrator` class:

```python
class Orchestrator:
    def __init__(self, config, db, messaging, incoming_queue):
        self._config = config
        self._db = db
        self._messaging = messaging
        self._incoming_queue = incoming_queue
        self._agent_registry = AgentRegistry()  # tracks running sub-agents
        self._ipc_bridge = IPCBridge(db, messaging, self._agent_registry)
        self._shutdown_event = asyncio.Event()
        self._wake_event = asyncio.Event()  # set by incoming messages

    async def run(self):
        """Main loop: fresh-session iterations until shutdown."""
        await self._ipc_bridge.start()
        message_task = asyncio.create_task(self._message_listener())
        try:
            while not self._shutdown_event.is_set():
                await self._run_iteration()
        finally:
            message_task.cancel()
            await self._ipc_bridge.stop()

    async def _run_iteration(self):
        """Single orchestrator iteration: snapshot → claude -p → done."""
        snapshot = await build_state_snapshot(self._db, self._agent_registry)
        system_prompt = load_system_prompt() + "\n\n" + snapshot

        mcp_config_path = self._write_mcp_config()

        session = ClaudeSession(
            prompt="Review the current state and decide what to do next.",
            cli_command=self._config.claude.cli_command,
            model="sonnet",  # routine iterations use Sonnet
            permission_mode="plan",  # read-only for built-in tools
            extra_flags=[
                "--append-system-prompt", system_prompt,
                "--mcp-config", str(mcp_config_path),
                "--strict-mcp-config",
                "--allowedTools", "Read Glob Grep Bash(git:log) Bash(git:status) Bash(git:diff)",
            ],
        )

        result = await session.run()
        await self._record_usage(result, "orchestrator")
        await self._handle_permission_denials(result)
        # Clean up temp mcp config
        mcp_config_path.unlink(missing_ok=True)

    async def _message_listener(self):
        """Drain incoming queue, wake the orchestrator on new messages."""
        while not self._shutdown_event.is_set():
            try:
                msg = await asyncio.wait_for(self._incoming_queue.get(), timeout=1.0)
                # Store in DB for the next iteration's snapshot
                await self._store_pending_message(msg)
                self._wake_event.set()  # Wake wait() early
            except TimeoutError:
                continue
```

**Key design points:**
- Each iteration is a complete `claude -p` session — no resume, no long-running context
- The `wait` tool (called by Claude via MCP) controls pacing — the Python loop doesn't add delays
- If Claude doesn't call `wait`, the next iteration starts immediately (useful for rapid response scenarios)
- Permission denials are forwarded to Telegram deterministically (Phase 4)
- The system prompt + state snapshot replaces the procedural task lifecycle

#### 2.4 Agent Registry (`foundation/agents/registry.py`)

Tracks running sub-agent processes:

```python
@dataclass
class AgentInfo:
    task_id: str
    process: asyncio.subprocess.Process
    session: ClaudeSession
    started_at: float
    mode: str  # "plan" or "execute"

class AgentRegistry:
    async def spawn(self, task_id, prompt, mode="plan") -> AgentInfo: ...
    async def cancel(self, task_id) -> bool: ...
    async def get_status(self, task_id) -> AgentInfo | None: ...
    async def list_active(self) -> list[AgentInfo]: ...
    async def collect_finished(self) -> list[tuple[str, ClaudeResult]]: ...
```

The registry monitors subprocess completion asynchronously. `collect_finished()` gathers results from any sub-agents that completed since the last call — these are included in the next iteration's state snapshot.

---

### Phase 3: Sub-Agent Integration

Wire up `spawn_agent` and `cancel_agent` to actually run coding sub-agents.

#### 3.1 Sub-Agent Lifecycle

When Claude calls `spawn_agent(task_id, prompt, mode="plan")`:
1. The IPC bridge creates a `ClaudeSession` with the specified prompt and mode
2. The session starts as a background asyncio task (non-blocking)
3. The `AgentRegistry` tracks it
4. The tool returns immediately with `{"status": "spawned", "task_id": "..."}`

When a sub-agent finishes:
1. `AgentRegistry.collect_finished()` returns the result
2. The state snapshot includes the completion
3. Claude sees it in the next iteration and decides what to do (update task, notify human, spawn next phase)

#### 3.2 Sub-Agent MCP Tools (separate from orchestrator tools)

Sub-agents get their own MCP server with a different tool set:
- `report_progress(step, status)` — writes to a progress file the orchestrator can read
- `request_decision(question, options)` — writes to a decisions file, pauses (for future HITL)
- `submit_plan(plan_json)` — writes structured plan for orchestrator review

For the MVP (unsandboxed, Foundation repo only), sub-agents run on the host with appropriate permission modes. The sub-agent MCP server is simpler than the orchestrator's — it writes to files rather than needing IPC.

---

### Phase 4: Permission Denial Forwarding

When the orchestrator's `claude -p` session tries a tool outside `--allowedTools`, Claude Code denies it and includes it in `permission_denials`.

#### 4.1 Parse Permission Denials

Extend `ClaudeResult` to include `permission_denials`:
```python
@dataclass(frozen=True)
class PermissionDenial:
    tool_name: str
    tool_use_id: str
    tool_input: dict

@dataclass(frozen=True)
class ClaudeResult:
    # ... existing fields ...
    permission_denials: list[PermissionDenial]
```

Parse from the result event's JSON output (the `permission_denials` array).

#### 4.2 Deterministic Forwarding

In `_handle_permission_denials`:
1. For each denial, format a Telegram message showing what Claude tried to do
2. Send with Approve/Deny buttons
3. If approved: resume the session with `--resume` and the approval
4. If denied: log and continue (Claude will see the denial in the next iteration if relevant)

This is **deterministic** — no AI decides whether to forward. Every denial goes to the human.

---

### Phase 5: Database & Schema Updates

#### 5.1 Schema V2 Migration

New tables:

```sql
-- Action log for state injection
CREATE TABLE IF NOT EXISTS action_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    iteration_id TEXT NOT NULL,
    action_type TEXT NOT NULL,  -- 'tool_call', 'message_sent', 'agent_spawned', etc.
    summary TEXT NOT NULL,
    details TEXT,  -- JSON blob with full details
    created_at TEXT NOT NULL
);

-- Pending messages from human (not yet processed by orchestrator)
CREATE TABLE IF NOT EXISTS pending_messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    text TEXT NOT NULL,
    received_at TEXT NOT NULL,
    processed INTEGER NOT NULL DEFAULT 0
);

-- Sub-agent session tracking
CREATE TABLE IF NOT EXISTS agent_sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id TEXT NOT NULL REFERENCES tasks(id),
    session_id TEXT,
    mode TEXT NOT NULL DEFAULT 'plan',
    status TEXT NOT NULL DEFAULT 'running'
        CHECK (status IN ('running', 'completed', 'failed', 'cancelled')),
    result_text TEXT,
    started_at TEXT NOT NULL,
    completed_at TEXT
);
```

#### 5.2 Task Status Updates

The task status CHECK constraint may need adjustment. The current set (`pending`, `planning`, `awaiting_approval`, `executing`, `complete`, `failed`, `cancelled`) still works, but the transitions are now driven by MCP tool calls rather than hardcoded Python. Claude calls `update_task(task_id, "planning")` instead of Python code doing it directly.

---

### Phase 6: CLI Wrapper Extensions

#### 6.1 New ClaudeSession Flags

Extend `ClaudeSession._build_command()` to support:
- `--mcp-config <path>` — path to MCP config JSON file
- `--strict-mcp-config` — restrict to only provided MCP servers
- `--allowedTools <list>` — whitelist built-in tools
- `--append-system-prompt <text>` — add to system prompt (keeps defaults)

These are passed via `extra_flags` today, but should become first-class parameters for clarity:

```python
class ClaudeSession:
    def __init__(
        self,
        prompt: str,
        *,
        # ... existing params ...
        mcp_config: Path | None = None,
        strict_mcp: bool = False,
        allowed_tools: list[str] | None = None,
        append_system_prompt: str | None = None,
    ):
```

#### 6.2 Permission Denials Parsing

The result event JSON may include `permission_denials`. Update the parser to extract these from the result event and include them in `ClaudeResult`.

---

### Phase 7: Testing

#### 7.1 MCP Server Unit Tests

Test each tool handler in isolation:
- `create_task` → verify DB record created
- `query_tasks` → verify correct filtering and JSON output
- `send_message` → verify IPC request sent correctly
- `read_memory` / `update_memory` → verify file I/O
- Tool input validation (missing fields, invalid types)

#### 7.2 IPC Bridge Tests

- Start bridge, send requests, verify responses
- Concurrent request handling
- Error handling (bridge down, invalid requests)

#### 7.3 Stub MCP Server

A test double that replaces the real MCP server for integration tests:
- Pre-programmed tool call sequences (like the existing Claude CLI stub)
- Records tool calls for assertion
- Returns canned responses

#### 7.4 Integration Tests — Fresh-Session Loop

Test the complete loop with stubs:
1. **Task creation flow:** Inject a Telegram message → verify `create_task` is called → verify task appears in DB
2. **Plan-approve-execute flow:** Pending task → orchestrator iteration spawns planning agent → agent completes → next iteration sends plan to human → human approves → next iteration spawns execution agent → agent completes → task marked complete
3. **Wait with early wake:** Orchestrator calls `wait(600)` → Telegram message arrives → wait returns early → next iteration processes the message
4. **Permission denial forwarding:** Orchestrator Claude tries forbidden tool → denial forwarded to Telegram → human responds

#### 7.5 Updated Claude CLI Stub

Extend the stub to:
- Accept `--mcp-config` and `--strict-mcp-config` flags (ignore gracefully)
- Accept `--allowedTools` flag
- Accept `--append-system-prompt` flag
- Include `permission_denials` in result events for denial-forwarding tests
- Support new fixture scenarios: `mcp_tool_calls` (simulate an orchestrator iteration that calls MCP tools)

---

## File Changes Summary

### New files
| File | Purpose |
|------|---------|
| `src/foundation/mcp/__init__.py` | MCP package |
| `src/foundation/mcp/server.py` | MCP server process (stdio transport) |
| `src/foundation/mcp/tools.py` | Tool handler implementations |
| `src/foundation/mcp/bridge.py` | IPC bridge (Unix socket server in daemon) |
| `src/foundation/mcp/protocol.py` | JSON-RPC message types for MCP |
| `src/foundation/agents/__init__.py` | Agents package |
| `src/foundation/agents/registry.py` | Sub-agent process tracking |
| `src/foundation/prompts/orchestrator.md` | Orchestrator system prompt |
| `src/foundation/state.py` | State snapshot builder |
| `tests/unit/test_mcp_tools.py` | MCP tool handler unit tests |
| `tests/unit/test_bridge.py` | IPC bridge unit tests |
| `tests/unit/test_state.py` | State snapshot builder tests |
| `tests/unit/test_registry.py` | Agent registry tests |
| `tests/integration/test_fresh_loop.py` | Fresh-session loop integration tests |
| `tests/integration/test_mcp_server.py` | MCP server integration tests |
| `tests/fixtures/stream-json/orchestrator-iteration.jsonl` | Fixture: orchestrator iteration with MCP tool calls |
| `tests/fixtures/stream-json/permission-denied.jsonl` | Fixture: session with permission denials |

### Modified files
| File | Changes |
|------|---------|
| `src/foundation/orchestrator.py` | Complete rewrite: fresh-session loop replaces procedural state machine |
| `src/foundation/claude/cli.py` | Add `mcp_config`, `strict_mcp`, `allowed_tools`, `append_system_prompt` params |
| `src/foundation/claude/events.py` | Add `PermissionDenial` dataclass |
| `src/foundation/claude/parser.py` | Parse `permission_denials` from result events |
| `src/foundation/db/schema.py` | Schema V2: `action_log`, `pending_messages`, `agent_sessions` tables |
| `src/foundation/db/tasks.py` | Minor: may add fields or queries used by MCP tools |
| `src/foundation/__main__.py` | Update `_run_daemon` to initialize new orchestrator components (IPC bridge, etc.) |
| `src/foundation/config.py` | Add orchestrator config (allowed_tools, system prompt path, memory dir) |
| `src/foundation/testing/claude_stub.py` | Support new CLI flags, permission denial fixtures |
| `tests/conftest.py` | New fixtures for MCP and agent registry testing |
| `tests/unit/test_orchestrator.py` | Rewrite tests for new orchestrator design |
| `tests/integration/test_lifecycle.py` | Update for fresh-session loop (may be replaced by test_fresh_loop.py) |
| `tests/integration/test_end_to_end.py` | Update for new orchestrator flow |

### Unchanged files
| File | Why |
|------|-----|
| `src/foundation/messaging/adapter.py` | ABC is stable — MCP tools call through it |
| `src/foundation/telegram/` | All Telegram code stays — MCP tools use it via IPC bridge |
| `src/foundation/db/connection.py` | Connection management unchanged |
| `src/foundation/db/usage.py` | Usage tracking stays — called from new orchestrator |
| `src/foundation/logging.py` | Unchanged |

---

## Implementation Order

The phases above describe logical groupings. The actual implementation order should be:

1. **CLI wrapper extensions** (Phase 6) — small, low-risk, enables everything else
2. **MCP protocol + server skeleton** (Phase 1.1) — get the stdio transport working
3. **Database schema V2** (Phase 5) — needed by tool handlers
4. **MCP tool handlers** (Phase 1.2) — implement each tool against real DB/filesystem
5. **IPC bridge** (Phase 1.3) — connect MCP server to daemon for messaging/agents
6. **Agent registry** (Phase 3) — sub-agent process management
7. **State snapshot builder** (Phase 2.1) — generates context for each iteration
8. **Orchestrator system prompt** (Phase 2.2) — SDM role and behavior definition
9. **New orchestrator loop** (Phase 2.3) — the main event: replace the state machine
10. **Permission denial forwarding** (Phase 4) — deterministic HITL path
11. **Tests throughout** (Phase 7) — unit tests with each component, integration tests after assembly

---

## Risks and Mitigations

| Risk | Impact | Mitigation |
|------|--------|------------|
| MCP server IPC adds latency | Tool calls feel slow | Unix domain sockets are fast (<1ms). Monitor and optimize if needed |
| Claude makes poor decisions in the loop | Tasks stall or loop | System prompt engineering + action log lets us detect patterns. Heuristic monitors (AD-9) run between iterations |
| `wait()` tool timing is wrong | Too frequent (burns tokens) or too slow (unresponsive) | Claude decides based on context. Default heuristics: min 30s, max 3600s. Override via config |
| State snapshot grows too large | Exceeds context window | Cap snapshot size. Summarize old entries. Use pagination for large task lists |
| MCP server crashes mid-session | Orchestrator iteration fails | Supervisor logic restarts. Iteration is idempotent — next iteration picks up cleanly |
| Sub-agent completes while orchestrator is mid-iteration | Result not seen until next iteration | Acceptable — the fresh-session loop handles this naturally. Results are stored in DB/registry and picked up next time |

---

## Verification Criteria

From the milestone definition in `docs/milestones.md`:

1. **Fresh-session loop works:** Orchestrator iteration receives a task → reasons about it → calls `spawn_agent` → calls `wait(60)` → next iteration checks result → calls `send_message` with plan → calls `request_human_input` for approval → next iteration resumes execution
2. **Early wake works:** Incoming Telegram message wakes the `wait` tool early
3. **Permission denial forwarding works:** Tool denial is forwarded to Telegram with Approve/Deny buttons
4. **All tests pass:** `tox run` passes all environments (format, lint, typecheck, test, audit)
