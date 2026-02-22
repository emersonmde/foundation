"""Usage tracking CRUD operations."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

import aiosqlite


@dataclass
class UsageRecord:
    """A usage record from the database."""

    id: int
    task_id: str | None
    session_id: str | None
    category: str
    model: str
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int
    cache_creation_tokens: int
    duration_ms: int
    created_at: str


@dataclass
class UsageSummary:
    """Aggregated usage statistics."""

    total_input_tokens: int
    total_output_tokens: int
    total_cache_read_tokens: int
    total_cache_creation_tokens: int
    total_duration_ms: int
    record_count: int


async def record_usage(
    db: aiosqlite.Connection,
    *,
    task_id: str | None = None,
    session_id: str | None = None,
    category: str = "coding",
    model: str = "",
    input_tokens: int = 0,
    output_tokens: int = 0,
    cache_read_tokens: int = 0,
    cache_creation_tokens: int = 0,
    duration_ms: int = 0,
) -> int:
    """Record a usage entry and return its ID."""
    now = datetime.now(UTC).isoformat()
    async with db.execute(
        """INSERT INTO usage_records
           (task_id, session_id, category, model, input_tokens, output_tokens,
            cache_read_tokens, cache_creation_tokens, duration_ms, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            task_id,
            session_id,
            category,
            model,
            input_tokens,
            output_tokens,
            cache_read_tokens,
            cache_creation_tokens,
            duration_ms,
            now,
        ),
    ) as cursor:
        row_id = cursor.lastrowid
    await db.commit()
    if row_id is None:
        raise RuntimeError("INSERT did not return a row ID")
    return row_id


async def get_usage_summary(
    db: aiosqlite.Connection,
    category: str | None = None,
    since: str | None = None,
) -> UsageSummary:
    """Get aggregated usage, optionally filtered by category and time."""
    conditions = []
    params: list[str] = []

    if category is not None:
        conditions.append("category = ?")
        params.append(category)
    if since is not None:
        conditions.append("created_at >= ?")
        params.append(since)

    where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
    query = f"""
        SELECT
            COALESCE(SUM(input_tokens), 0) as total_input,
            COALESCE(SUM(output_tokens), 0) as total_output,
            COALESCE(SUM(cache_read_tokens), 0) as total_cache_read,
            COALESCE(SUM(cache_creation_tokens), 0) as total_cache_creation,
            COALESCE(SUM(duration_ms), 0) as total_duration,
            COUNT(*) as cnt
        FROM usage_records {where}
    """
    async with db.execute(query, params) as cursor:
        row = await cursor.fetchone()
        if row is None:
            raise RuntimeError("Aggregate query returned no rows")
        return UsageSummary(
            total_input_tokens=row[0],
            total_output_tokens=row[1],
            total_cache_read_tokens=row[2],
            total_cache_creation_tokens=row[3],
            total_duration_ms=row[4],
            record_count=row[5],
        )
