# Research: Claude Code CLI for Programmatic Use

## Key CLI Flags for Automation

| Flag | Purpose |
|---|---|
| `-p, --print` | Non-interactive headless mode (required for automation) |
| `--output-format stream-json` | NDJSON streaming output with structured events |
| `--output-format json` | Single JSON result object at completion |
| `--input-format stream-json` | Bidirectional streaming input |
| `--include-partial-messages` | Real-time text chunks as they generate (requires stream-json) |
| `--permission-mode plan` | Read-only mode — agent can explore but not modify |
| `--permission-mode acceptEdits` | Auto-accept file edits, still prompt for bash |
| `--permission-mode bypassPermissions` | Skip all permission checks (equivalent to `--dangerously-skip-permissions`) |
| `--resume <session-id>` | Continue a previous conversation with full context |
| `--fork-session` | When resuming, create a new session ID (branch the conversation) |
| `--session-id <uuid>` | Use a specific session ID |
| `--no-session-persistence` | Don't save session to disk (ephemeral) |
| `--max-turns N` | Limit agentic turns. Exits with error when reached |
| `--max-budget-usd N` | Cost cap per invocation |
| `--model <alias>` | Model selection: `sonnet`, `opus`, or full model ID |
| `--fallback-model <model>` | Auto-fallback when primary model is overloaded |
| `--allowedTools` | Whitelist tools (supports `Bash(git:*)` glob patterns) |
| `--disallowedTools` | Blacklist specific tools |
| `--tools` | Control which built-in tools are loaded (`""` disables all) |
| `--append-system-prompt` | Add to default system prompt (keeps built-in instructions) |
| `--system-prompt` | Replace entire system prompt (removes all defaults) |
| `--mcp-config` | Load MCP servers from JSON files or strings |
| `--strict-mcp-config` | Only use MCP servers from `--mcp-config`, ignore all others |
| `--json-schema` | Force structured JSON output matching a schema |
| `--worktree, -w` | Start in an isolated git worktree |
| `--agents` | Define custom subagents via JSON |
| `--agent` | Select which agent to use |

## Stream-JSON Output Format

NDJSON (newline-delimited JSON). Each line is a complete JSON object.

### Event Types

**Init event** — emitted once at session start:
```jsonc
{
  "type": "system",
  "subtype": "init",
  "session_id": "550e8400-e29b-41d4-a716-446655440000",
  "tools": [...]
}
```
This is where you capture the `session_id` for later `--resume`.

**Assistant message** — model output (text and tool calls):
```jsonc
{
  "type": "assistant",
  "message": {
    "role": "assistant",
    "content": [
      {"type": "text", "text": "I'll read the file..."},
      {"type": "tool_use", "id": "toolu_xxx", "name": "Read", "input": {"file_path": "/src/main.py"}}
    ]
  }
}
```

**Tool result** — output from tool execution:
```jsonc
{
  "type": "tool_result",
  "tool_use_id": "toolu_xxx",
  "content": "file contents here..."
}
```

**Result event** — emitted once at completion:
```jsonc
{
  "type": "result",
  "result": "final assistant text",
  "session_id": "550e8400-...",
  "status": "success",
  "duration_ms": 12345
}
```

With `--include-partial-messages`, you also get incremental text chunks as they stream, enabling real-time progress display.

## Session Lifecycle

### Plan-then-Execute via Resume

This is the recommended orchestration pattern:

1. **Planning phase:** Run with `--permission-mode plan` to get a read-only exploration and plan.
2. **Capture session ID** from the `system/init` event.
3. **Send plan to human** for review.
4. **Execution phase:** Resume the same session with `--resume <session-id>` and different permission mode (e.g., `--dangerously-skip-permissions` inside sandbox).

The agent retains full context from the planning phase — files it read, analysis it did, the plan it wrote — and can immediately begin executing.

### Session Storage

Sessions stored in `~/.claude/projects/<project-hash>/sessions/<session-id>/`. These are JSON files with the full conversation history. Use `--no-session-persistence` for ephemeral automation runs.

## Permission Modes

| Mode | Behavior |
|---|---|
| `default` | Prompts for write operations |
| `plan` | Read-only. Allows Read, Glob, Grep. Blocks Bash, Edit, Write. Model produces a plan in natural language. |
| `acceptEdits` | Auto-accepts Edit/Write, prompts for Bash |
| `dontAsk` | Auto-accepts everything through normal permission framework |
| `bypassPermissions` | Skips all permission checks entirely |

### Plan Mode Details

- Plan is embedded in the assistant's response text (no structured format).
- To get structured plans, combine with `--json-schema` to force a specific output format.
- Allowed tools: Read, Glob, Grep (and WebFetch/WebSearch if configured).
- Blocked tools: Bash, Edit, Write, NotebookEdit, EnterWorktree.

## AllowedTools Syntax

```bash
# Simple tool names (space or comma separated)
--allowedTools "Read Edit Glob Grep"

# Bash with command pattern filtering
--allowedTools "Bash(git:*) Bash(npm:*) Read Edit"

# Bash(pattern) restricts which commands can be run:
# Bash(git:*)      → only commands starting with "git"
# Bash(cargo:test) → only "cargo test"
# Bash(npm:*)      → only commands starting with "npm"
```

## MCP Integration for Agent-Orchestrator Communication

Custom MCP tools can be injected via `--mcp-config`. This enables agents to communicate back to the orchestrator:

```json
{
  "mcpServers": {
    "orchestrator": {
      "command": "python",
      "args": ["mcp_bridge.py"],
      "env": {"TASK_ID": "task-123"}
    }
  }
}
```

Combined with `--strict-mcp-config`, this ensures agents only use the orchestrator's MCP tools. Potential tools:
- `report_progress(step, status)` — agent reports what it's doing
- `request_decision(question, options)` — agent asks orchestrator for input
- `submit_plan(plan_json)` — structured plan submission
- `pty_start(command)`, `pty_send(session, input)`, `pty_read(session)` — interactive process management

## Custom Agents via `--agents`

```bash
claude --agents '{
  "reviewer": {
    "description": "Expert code reviewer",
    "prompt": "You are a senior code reviewer. Focus on quality and security.",
    "tools": ["Read", "Grep", "Glob", "Bash"],
    "model": "sonnet"
  }
}' --agent reviewer -p "Review changes in src/"
```

Agent definition fields: `description`, `prompt`, `tools`, `disallowedTools`, `model`, `skills`, `mcpServers`, `maxTurns`.

## Max-Turns Behavior

When `--max-turns N` is reached, the session exits with an error. The result event will have an error status. The session can still be resumed later with `--resume`.

## Nesting Limitation

`claude -p` cannot be launched inside another Claude Code session (detects `CLAUDECODE` env var and refuses). The orchestrator daemon must unset this variable or never run inside Claude Code itself. Inside Docker containers this is not an issue since the container is a fresh environment.

## Non-Interactive Behavior (`-p` mode)

Tested 2026-02-22. These behaviors are critical for orchestrator prompt engineering.

### Permission Denials

When a tool requires permission that hasn't been granted, `-p` mode does **not** block waiting for input. Instead:

1. The tool call is immediately denied
2. The agent sees the denial and may retry or give up
3. The final JSON output includes a `permission_denials` array with the full tool call details:

```jsonc
{
  "permission_denials": [
    {
      "tool_name": "Write",
      "tool_use_id": "toolu_01Ei6NLg...",
      "tool_input": {
        "file_path": "/path/to/file.txt",
        "content": "file contents the agent wanted to write"
      }
    }
  ]
}
```

The orchestrator can see exactly what the agent tried to do, including full inputs, even when permissions blocked it. Useful for plan-mode validation or building an approval loop.

### AskUserQuestion as an Escalation Channel

`AskUserQuestion` is immediately denied in `-p` mode (no interactive terminal). The questions and options appear in `permission_denials` with the full structured input — question text, options with labels and descriptions, and the multiSelect flag.

This is a **feature for orchestration**, not just a limitation. When a sub-agent hits genuine ambiguity or a significant design decision, the denied `AskUserQuestion` gives the orchestrator a structured escalation signal:

1. Agent encounters ambiguity or a one-way-door decision during execution
2. Agent calls `AskUserQuestion` — immediately denied, appears in `permission_denials`
3. Orchestrator parses the structured question from the JSON output
4. Orchestrator forwards the question to the human via Telegram (inline keyboard maps naturally to the options)
5. Human answers on their phone
6. Orchestrator resumes the session with `--resume <session-id>`, providing the answer in the prompt
7. Agent has full context of what it asked and proceeds with the human's decision

This means the orchestrator doesn't need a custom MCP tool for agent-to-human escalation — the built-in `AskUserQuestion` denial already provides a structured format with typed options. The orchestrator just needs to detect it in the output and bridge it to Telegram.

**Prompt engineering consideration:** Agents should be instructed to distinguish between:
- **Two-way-door decisions** (easily reversible) — make a reasonable choice and move on
- **One-way-door decisions** (hard to reverse, architectural) — use `AskUserQuestion` to escalate

This maps directly to the variable autonomy levels in the requirements.

### Resume is Directory-Scoped

`--resume <session-id>` fails with "No conversation found" if the working directory has changed since the session was created. Sessions are stored under `~/.claude/projects/<project-hash>/`, and the project hash is derived from the working directory. The orchestrator must ensure it resumes from the same directory the session was started in.

### Strategies for Tool Permissions in Automation

| Strategy | When to use |
|---|---|
| `--allowedTools "Write Edit Bash(git:*)"` | Granular control — whitelist specific tools/commands |
| `--permission-mode bypassPermissions` | Inside a Docker sandbox where the sandbox IS the permission boundary |
| `--permission-mode acceptEdits` | Trust file edits, still gate bash commands |
| Resume loop parsing `permission_denials` | When the orchestrator wants to approve each action individually |

### Implications for Orchestrator Prompts

- For planning agents, `--permission-mode plan` naturally blocks all writes — no extra config needed
- For execution agents inside Docker, `--permission-mode bypassPermissions` is safe since the container is the boundary
- Use `--append-system-prompt` to inject task context, coding conventions, and decision-making guidelines
- `--max-turns` provides a hard stop if an agent loops — the session can still be resumed later
- Agents should be coached on when to escalate (one-way doors) vs. decide autonomously (two-way doors)

## Model Selection

- `--model sonnet` or `--model opus` for latest aliases
- `--model claude-sonnet-4-6` for specific model IDs
- `--fallback-model sonnet` for automatic fallback when primary is overloaded
- These control the model for the entire session

## Bidirectional Streaming: `--input-format stream-json`

Accepts NDJSON on stdin, enabling multi-turn conversations within a single process:

```json
{"type": "user", "message": {"role": "user", "content": "Your message"}, "session_id": "default"}
```

Messages queue and process sequentially. The first message uses `"session_id": "default"`; subsequent messages use the session ID from the output stream.

### Limitations (as of February 2026)

**Cannot provide `tool_result` for pending calls.** When an agent calls `AskUserQuestion` and it's denied, there's no way to send the answer back via stdin as a `tool_result`. The CLI injects a synthetic "No response requested" message that breaks the conversation chain. ([Issue #16712](https://github.com/anthropics/claude-code/issues/16712))

**Known reliability issues:**
- Sending a second user message can cause the process to hang ([Issue #3187](https://github.com/anthropics/claude-code/issues/3187), resolved but suggests fragility)
- Session `.jsonl` files get duplicate entries that grow exponentially with each message ([Issue #5034](https://github.com/anthropics/claude-code/issues/5034))

**Verdict for Foundation:** The resume loop (`permission_denials` → Telegram → `--resume` with answer) is more reliable than raw `--input-format stream-json` for HITL. The Agent SDK (`claude-agent-sdk`) wraps this same mechanism but adds proper `canUseTool` callbacks that pause execution cleanly — that's the right path for real-time HITL when we add the SDK backend (see AD-14).

### `--permission-prompt-tool`

Delegates permission decisions to an MCP tool. An alternative to `canUseTool` callbacks for programmatic permission handling without the Agent SDK. Worth investigating as a potential improvement to the CLI backend's HITL mechanism.

## Session Lifetime and Persistence

### Sessions do NOT expire — there is no TTL

The Anthropic Messages API is entirely stateless. Sessions are local `.jsonl` transcript files in `~/.claude/projects/<project-hash>/`. When `--resume` is used, Claude Code reads the transcript and replays the full conversation history to the API. Sessions can theoretically live forever.

### Why `--resume` fails ("No conversation found")

Five known root causes — none are TTL:

1. **Working directory mismatch:** Sessions are scoped by `cwd`. The project hash in the storage path is derived from the working directory. Resuming from a different directory fails. ([Issue #5768](https://github.com/anthropics/claude-code/issues/5768))

2. **`cleanupPeriodDays` cleanup:** Claude Code deletes session files older than `cleanupPeriodDays` (default: 30 days) on startup. **Do NOT set to 0** — this disables transcript persistence entirely (bug, [Issue #23710](https://github.com/anthropics/claude-code/issues/23710)). Set to `99999` to effectively disable cleanup.

3. **`sessions-index.json` desync:** The resume picker reads an index file that can get out of sync with actual `.jsonl` files on disk. Direct `--resume <session-id>` bypasses the picker. ([Issue #18311](https://github.com/anthropics/claude-code/issues/18311))

4. **Context compaction stripping headers:** When Claude Code compacts a long conversation, it can strip the `system` header from the `.jsonl` file, making the session unfindable by the resume mechanism.

5. **Process killed before flush:** SIGTERM/SIGKILL before the transcript is written to disk leaves an incomplete or missing `.jsonl` file. ([Issue #12730](https://github.com/anthropics/claude-code/issues/12730))

### Implications for the orchestrator

The plan-then-execute pattern (plan → human approval via Telegram → resume for execution) can involve 30+ minute gaps. This is safe because there's no TTL.

**Required mitigations:**
- Set `cleanupPeriodDays: 99999` in `~/.claude/settings.json` on the deployment machine
- Always pass explicit `cwd` to `asyncio.create_subprocess_exec()` matching the project's repo path
- Handle resume failure gracefully: if `--resume` fails, fall back to a fresh session with the plan included in the prompt
- Use graceful cancellation (SIGTERM + wait) rather than SIGKILL to ensure transcripts are flushed
