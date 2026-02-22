"""SQLite connection management — WAL mode, foreign keys, row factory."""

from __future__ import annotations

from pathlib import Path

import aiosqlite


async def connect(db_path: Path) -> aiosqlite.Connection:
    """Open a SQLite connection with WAL mode and foreign keys enabled.

    The caller is responsible for closing the connection.
    """
    db_path.parent.mkdir(parents=True, exist_ok=True)
    db = await aiosqlite.connect(db_path)
    db.row_factory = aiosqlite.Row
    async with db.execute("PRAGMA journal_mode=WAL") as cursor:
        row = await cursor.fetchone()
        if row is None or row[0] != "wal":
            raise RuntimeError(f"Failed to enable WAL mode (got {row})")
    await db.execute("PRAGMA foreign_keys=ON")
    return db
