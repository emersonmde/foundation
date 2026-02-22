"""SQLite schema creation and versioning."""

from __future__ import annotations

import aiosqlite

CURRENT_VERSION = 1

_SCHEMA_V1 = """
CREATE TABLE IF NOT EXISTS schema_version (
    version INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS tasks (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN (
            'pending', 'planning', 'awaiting_approval',
            'executing', 'complete', 'failed', 'cancelled'
        )),
    session_id TEXT,
    plan_text TEXT,
    result_text TEXT,
    error_message TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    completed_at TEXT
);

CREATE TABLE IF NOT EXISTS usage_records (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id TEXT REFERENCES tasks(id),
    session_id TEXT,
    category TEXT NOT NULL DEFAULT 'coding'
        CHECK (category IN ('orchestrator', 'coding')),
    model TEXT NOT NULL DEFAULT '',
    input_tokens INTEGER NOT NULL DEFAULT 0,
    output_tokens INTEGER NOT NULL DEFAULT 0,
    cache_read_tokens INTEGER NOT NULL DEFAULT 0,
    cache_creation_tokens INTEGER NOT NULL DEFAULT 0,
    duration_ms INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);
"""


async def get_schema_version(db: aiosqlite.Connection) -> int:
    """Get the current schema version, or 0 if no schema exists."""
    try:
        query = "SELECT version FROM schema_version ORDER BY version DESC LIMIT 1"
        async with db.execute(query) as cursor:
            row = await cursor.fetchone()
            return row[0] if row else 0
    except aiosqlite.OperationalError:
        return 0


async def apply_schema(db: aiosqlite.Connection) -> None:
    """Create or migrate the database schema to the current version."""
    version = await get_schema_version(db)

    if version < 1:
        await db.executescript(_SCHEMA_V1)
        await db.execute(
            "INSERT INTO schema_version (version) SELECT 1"
            " WHERE NOT EXISTS (SELECT 1 FROM schema_version WHERE version = 1)"
        )
        await db.commit()
