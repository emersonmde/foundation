"""State snapshot builder — generates context for each orchestrator iteration."""

from __future__ import annotations

import aiosqlite

from foundation.agents.registry import AgentRegistry
from foundation.db.tasks import list_tasks
from foundation.db.usage import get_usage_summary


async def build_state_snapshot(
    db: aiosqlite.Connection,
    agent_registry: AgentRegistry,
    *,
    action_log_limit: int = 10,
    pending_limit: int = 10,
) -> str:
    """Build a text summary of current state for the orchestrator's context.

    The snapshot is injected into the system prompt for each fresh-session iteration.
    """
    sections: list[str] = []

    # --- Tasks ---
    tasks = await list_tasks(db)
    if tasks:
        lines = ["### Tasks"]
        for t in tasks:
            age = ""
            if t.status in ("complete", "failed", "cancelled"):
                age = f" — completed {t.completed_at or 'unknown'}"
            lines.append(f'- [{t.status}] "{t.title}" (id: {t.id[:8]}...){age}')
        sections.append("\n".join(lines))
    else:
        sections.append("### Tasks\nNo tasks.")

    # --- Active sub-agents ---
    active_agents = agent_registry.list_active()
    if active_agents:
        lines = ["### Active Sub-Agents"]
        for info in active_agents:
            elapsed = int(info.elapsed_seconds)
            mins = elapsed // 60
            secs = elapsed % 60
            lines.append(f"- task {info.task_id[:8]}: {info.mode} mode, running {mins}m{secs}s")
        sections.append("\n".join(lines))

    # --- Finished agents (not yet collected) ---
    finished = agent_registry.collect_finished()
    if finished:
        lines = ["### Recently Completed Agents"]
        for task_id, result in finished:
            status_str = result.status
            text_preview = result.result_text[:200] if result.result_text else "(no output)"
            lines.append(f"- task {task_id[:8]}: {status_str} — {text_preview}")
        sections.append("\n".join(lines))

    # --- Recent actions ---
    async with db.execute(
        "SELECT action_type, summary FROM action_log ORDER BY id DESC LIMIT ?",
        (action_log_limit,),
    ) as cursor:
        rows = await cursor.fetchall()
    if rows:
        lines = ["### Recent Actions"]
        for row in list(rows)[::-1]:  # oldest first
            lines.append(f"- {dict(row)['summary']}")
        sections.append("\n".join(lines))

    # --- Pending messages ---
    async with db.execute(
        "SELECT text, received_at FROM pending_messages WHERE processed = 0"
        " ORDER BY id ASC LIMIT ?",
        (pending_limit,),
    ) as cursor:
        rows = await cursor.fetchall()
    if rows:
        lines = ["### Pending Messages from Human"]
        for row in rows:
            r = dict(row)
            lines.append(f'- "{r["text"]}" (received {r["received_at"]})')
        sections.append("\n".join(lines))

    # --- Budget status ---
    usage = await get_usage_summary(db)
    if usage.record_count > 0:
        lines = ["### Token Usage"]
        total = usage.total_input_tokens + usage.total_output_tokens
        lines.append(f"- Total tokens: {total:,} ({usage.record_count} sessions)")
        sections.append("\n".join(lines))

    return "## Current State\n\n" + "\n\n".join(sections)
