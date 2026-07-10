from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from shared.memory_store import MemoryStore


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class SQLiteMemoryStore(MemoryStore):
    """SQLite implementation used locally and as a Redis fallback."""

    def __init__(self, *, data_dir: Path, db_path: Path) -> None:
        self.data_dir = Path(data_dir)
        self.db_path = Path(db_path)

    def _connection(self) -> sqlite3.Connection:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.db_path)
        connection.row_factory = sqlite3.Row
        return connection

    def _init_schema(self) -> None:
        with self._connection() as connection:
            connection.execute(
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
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS conversation_turns (
                    id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL,
                    trace_id TEXT,
                    user_message TEXT NOT NULL,
                    assistant_message TEXT NOT NULL,
                    route TEXT,
                    sources_json TEXT,
                    created_at TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_conversation_turns_session_created
                ON conversation_turns(session_id, created_at)
                """
            )
            connection.commit()

    def save_memory(
        self,
        *,
        key: str,
        value: str,
        memory_type: str,
        source_agent: str,
    ) -> str:
        self._init_schema()
        memory_id = str(uuid.uuid4())

        with self._connection() as connection:
            connection.execute(
                """
                INSERT INTO agent_memories (
                    id, memory_type, key, value, source_agent, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    memory_id,
                    memory_type,
                    key,
                    value,
                    source_agent,
                    _now_iso(),
                ),
            )
            connection.commit()

        return memory_id

    def search_memories(self, *, query: str, limit: int) -> list[dict[str, Any]]:
        self._init_schema()
        pattern = f"%{query}%"

        with self._connection() as connection:
            rows = connection.execute(
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

    def list_memories(self, *, limit: int) -> list[dict[str, Any]]:
        self._init_schema()

        with self._connection() as connection:
            rows = connection.execute(
                """
                SELECT id, memory_type, key, value, source_agent, created_at
                FROM agent_memories
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()

        return [dict(row) for row in rows]

    def delete_memory(self, *, memory_id: str) -> bool:
        self._init_schema()

        with self._connection() as connection:
            cursor = connection.execute(
                "DELETE FROM agent_memories WHERE id = ?",
                (memory_id,),
            )
            connection.commit()

        return cursor.rowcount > 0

    def save_conversation_turn(
        self,
        *,
        session_id: str,
        user_message: str,
        assistant_message: str,
        trace_id: str | None,
        route: str | None,
        sources: list[dict[str, Any]],
    ) -> str:
        self._init_schema()
        turn_id = str(uuid.uuid4())

        with self._connection() as connection:
            connection.execute(
                """
                INSERT INTO conversation_turns (
                    id, session_id, trace_id, user_message,
                    assistant_message, route, sources_json, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    turn_id,
                    session_id,
                    trace_id,
                    user_message,
                    assistant_message,
                    route,
                    json.dumps(sources, ensure_ascii=False),
                    _now_iso(),
                ),
            )
            connection.commit()

        return turn_id

    @staticmethod
    def _conversation_row(row: sqlite3.Row) -> dict[str, Any]:
        item = dict(row)
        try:
            item["sources"] = json.loads(item.pop("sources_json") or "[]")
        except json.JSONDecodeError:
            item["sources"] = []
        return item

    def get_recent_conversation_turns(
        self,
        *,
        session_id: str,
        limit: int,
    ) -> list[dict[str, Any]]:
        self._init_schema()

        with self._connection() as connection:
            rows = connection.execute(
                """
                SELECT id, session_id, trace_id, user_message,
                       assistant_message, route, sources_json, created_at
                FROM conversation_turns
                WHERE session_id = ?
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (session_id, limit),
            ).fetchall()

        return [self._conversation_row(row) for row in reversed(rows)]

    def list_conversation_turns(self, *, limit: int) -> list[dict[str, Any]]:
        self._init_schema()

        with self._connection() as connection:
            rows = connection.execute(
                """
                SELECT id, session_id, trace_id, user_message,
                       assistant_message, route, sources_json, created_at
                FROM conversation_turns
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()

        return [self._conversation_row(row) for row in rows]

    def health(self) -> dict[str, Any]:
        try:
            self._init_schema()
            with self._connection() as connection:
                connection.execute("SELECT 1").fetchone()
            return {
                "backend": "sqlite",
                "available": True,
                "db_path": str(self.db_path),
            }
        except Exception as exc:
            return {
                "backend": "sqlite",
                "available": False,
                "db_path": str(self.db_path),
                "error": f"{type(exc).__name__}: {exc}",
            }
