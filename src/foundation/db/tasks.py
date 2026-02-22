"""Task queue CRUD operations."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

import aiosqlite


@dataclass
class Task:
    """A task record from the database."""

    id: str
    title: str
    description: str
    status: str
    session_id: str | None
    plan_text: str | None
    result_text: str | None
    error_message: str | None
    created_at: str
    updated_at: str
    completed_at: str | None


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


async def create_task(
    db: aiosqlite.Connection,
    title: str,
    description: str = "",
) -> Task:
    """Create a new task and return it."""
    task_id = str(uuid.uuid4())
    now = _now_iso()
    await db.execute(
        """INSERT INTO tasks (id, title, description, status, created_at, updated_at)
           VALUES (?, ?, ?, 'pending', ?, ?)""",
        (task_id, title, description, now, now),
    )
    await db.commit()
    return Task(
        id=task_id,
        title=title,
        description=description,
        status="pending",
        session_id=None,
        plan_text=None,
        result_text=None,
        error_message=None,
        created_at=now,
        updated_at=now,
        completed_at=None,
    )


async def get_task(db: aiosqlite.Connection, task_id: str) -> Task | None:
    """Fetch a task by ID, or None if not found."""
    async with db.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)) as cursor:
        row = await cursor.fetchone()
        if row is None:
            return None
        return Task(**dict(row))


async def update_task_status(
    db: aiosqlite.Connection,
    task_id: str,
    status: str,
    *,
    session_id: str | None = None,
    plan_text: str | None = None,
    result_text: str | None = None,
    error_message: str | None = None,
) -> None:
    """Update a task's status and optional fields."""
    now = _now_iso()
    completed_at = now if status in ("complete", "failed", "cancelled") else None

    sets = ["status = ?", "updated_at = ?"]
    params: list[str | None] = [status, now]

    if session_id is not None:
        sets.append("session_id = ?")
        params.append(session_id)
    if plan_text is not None:
        sets.append("plan_text = ?")
        params.append(plan_text)
    if result_text is not None:
        sets.append("result_text = ?")
        params.append(result_text)
    if error_message is not None:
        sets.append("error_message = ?")
        params.append(error_message)
    if completed_at is not None:
        sets.append("completed_at = ?")
        params.append(completed_at)

    params.append(task_id)
    await db.execute(f"UPDATE tasks SET {', '.join(sets)} WHERE id = ?", params)
    await db.commit()


async def list_tasks(
    db: aiosqlite.Connection,
    status: str | None = None,
) -> list[Task]:
    """List tasks, optionally filtered by status."""
    if status is not None:
        query = "SELECT * FROM tasks WHERE status = ? ORDER BY created_at DESC"
        params: tuple[str, ...] = (status,)
    else:
        query = "SELECT * FROM tasks ORDER BY created_at DESC"
        params = ()
    async with db.execute(query, params) as cursor:
        rows = await cursor.fetchall()
        return [Task(**dict(row)) for row in rows]
