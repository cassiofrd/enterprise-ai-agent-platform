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



# ---------------------------------------------------------------------------
# Conversation memory
# ---------------------------------------------------------------------------

def init_conversation_db() -> None:
    """Create the lightweight conversation memory table used by the Supervisor."""
    init_memory_db()
    with get_connection() as conn:
        conn.execute(
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
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_conversation_turns_session_created
            ON conversation_turns(session_id, created_at)
            """
        )
        conn.commit()


def save_conversation_turn(
    *,
    session_id: str,
    user_message: str,
    assistant_message: str,
    trace_id: str | None = None,
    route: str | None = None,
    sources: list[dict[str, Any]] | None = None,
) -> str:
    """Persist one conversational turn for short conversation memory and audit."""
    import json

    init_conversation_db()
    turn_id = str(uuid.uuid4())

    with get_connection() as conn:
        conn.execute(
            """
            INSERT INTO conversation_turns (
                id, session_id, trace_id, user_message, assistant_message,
                route, sources_json, created_at
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
                json.dumps(sources or [], ensure_ascii=False),
                now_iso(),
            ),
        )
        conn.commit()

    return turn_id


def get_recent_conversation_turns(session_id: str, limit: int = 6) -> list[dict[str, Any]]:
    """Return recent turns in chronological order for a specific session."""
    import json

    init_conversation_db()

    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT id, session_id, trace_id, user_message, assistant_message,
                   route, sources_json, created_at
            FROM conversation_turns
            WHERE session_id = ?
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (session_id, limit),
        ).fetchall()

    turns: list[dict[str, Any]] = []
    for row in reversed(rows):
        item = dict(row)
        try:
            item["sources"] = json.loads(item.pop("sources_json") or "[]")
        except json.JSONDecodeError:
            item["sources"] = []
        turns.append(item)

    return turns


def format_conversation_context(session_id: str, limit: int = 6) -> str:
    """Format recent conversation turns for prompt injection."""
    turns = get_recent_conversation_turns(session_id=session_id, limit=limit)
    if not turns:
        return "Sem histórico recente para esta sessão."

    lines: list[str] = []
    for idx, turn in enumerate(turns, start=1):
        route = turn.get("route") or "unknown"
        lines.append(f"Turno {idx} | rota={route}")
        lines.append(f"Usuário: {turn.get('user_message', '')}")
        lines.append(f"Assistente: {turn.get('assistant_message', '')}")

    return "\n".join(lines)


def list_conversation_turns(limit: int = 50) -> list[dict[str, Any]]:
    """Return recent turns across sessions for audit/debug endpoints."""
    import json

    init_conversation_db()
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT id, session_id, trace_id, user_message, assistant_message,
                   route, sources_json, created_at
            FROM conversation_turns
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()

    results: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        try:
            item["sources"] = json.loads(item.pop("sources_json") or "[]")
        except json.JSONDecodeError:
            item["sources"] = []
        results.append(item)

    return results
