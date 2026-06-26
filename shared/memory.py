from __future__ import annotations

import sqlite3
import uuid
from datetime import datetime, timezone
from typing import Any

from shared.config import DATA_DIR, MEMORY_DB_PATH


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def get_connection() -> sqlite3.Connection:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(MEMORY_DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_memory_db() -> None:
    with get_connection() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS agent_memories (
                id TEXT PRIMARY KEY,
                memory_type TEXT NOT NULL,
                key TEXT NOT NULL,
                value TEXT NOT NULL,
                source_agent TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )
        conn.commit()


def save_memory(
    key: str,
    value: str,
    memory_type: str = "fact",
    source_agent: str = "inventory",
) -> str:
    init_memory_db()
    memory_id = str(uuid.uuid4())

    with get_connection() as conn:
        conn.execute(
            """
            INSERT INTO agent_memories (
                id, memory_type, key, value, source_agent, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (memory_id, memory_type, key, value, source_agent, now_iso()),
        )
        conn.commit()

    return memory_id


def search_memories(query: str, limit: int = 10) -> list[dict[str, Any]]:
    init_memory_db()
    pattern = f"%{query}%"

    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT id, memory_type, key, value, source_agent, created_at
            FROM agent_memories
            WHERE key LIKE ? OR value LIKE ?
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (pattern, pattern, limit),
        ).fetchall()

    return [dict(row) for row in rows]


def list_memories(limit: int = 50) -> list[dict[str, Any]]:
    init_memory_db()

    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT id, memory_type, key, value, source_agent, created_at
            FROM agent_memories
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()

    return [dict(row) for row in rows]

def delete_memory(memory_id: str) -> bool:
    init_memory_db()

    with get_connection() as conn:
        cursor = conn.execute(
            """
            DELETE FROM agent_memories
            WHERE id = ?
            """,
            (memory_id,),
        )
        conn.commit()

    return cursor.rowcount > 0

