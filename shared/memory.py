from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from shared.cache import get_redis_client
from shared.config import DATA_DIR, MEMORY_DB_PATH
from shared.observability import log_event
from shared.redis_memory_store import RedisMemoryStore
from shared.settings import settings
from shared.sqlite_memory_store import SQLiteMemoryStore


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sqlite_store() -> SQLiteMemoryStore:
    # Read module globals on every call so existing tests can monkeypatch paths.
    return SQLiteMemoryStore(
        data_dir=DATA_DIR,
        db_path=MEMORY_DB_PATH,
    )


def _redis_store() -> RedisMemoryStore | None:
    client = get_redis_client()
    if client is None:
        return None

    return RedisMemoryStore(
        client=client,
        key_prefix=getattr(
            settings,
            "memory_key_prefix",
            "enterprise-ai-agent-memory",
        ),
        conversation_ttl_seconds=getattr(
            settings,
            "conversation_memory_ttl_seconds",
            604800,
        ),
        long_term_ttl_seconds=getattr(
            settings,
            "long_term_memory_ttl_seconds",
            0,
        ),
    )


def _selected_store():
    backend = getattr(settings, "memory_backend", "auto").strip().lower()

    if backend in {"auto", "redis"}:
        store = _redis_store()
        if store is not None:
            return store

        if backend == "redis":
            log_event(
                "memory.redis.unavailable",
                requested_backend="redis",
                fallback_backend="sqlite",
            )

    return _sqlite_store()


def memory_backend() -> str:
    return _selected_store().health().get("backend", "sqlite")


def memory_health() -> dict[str, Any]:
    store = _selected_store()
    status = store.health()
    status["requested_backend"] = getattr(settings, "memory_backend", "auto")
    status["fallback_enabled"] = True
    return status


def init_memory_db() -> None:
    # Preserved for backward compatibility with callers and tests.
    _sqlite_store()._init_schema()


def init_conversation_db() -> None:
    _sqlite_store()._init_schema()


def save_memory(
    key: str,
    value: str,
    memory_type: str = "fact",
    source_agent: str = "inventory",
) -> str:
    store = _selected_store()
    memory_id = store.save_memory(
        key=key,
        value=value,
        memory_type=memory_type,
        source_agent=source_agent,
    )
    log_event(
        "memory.store.save",
        backend=store.health().get("backend"),
        memory_id=memory_id,
        memory_type=memory_type,
        source_agent=source_agent,
    )
    return memory_id


def search_memories(query: str, limit: int = 10) -> list[dict[str, Any]]:
    store = _selected_store()
    results = store.search_memories(query=query, limit=limit)
    log_event(
        "memory.store.search",
        backend=store.health().get("backend"),
        query=query,
        result_count=len(results),
    )
    return results


def list_memories(limit: int = 50) -> list[dict[str, Any]]:
    store = _selected_store()
    return store.list_memories(limit=limit)


def delete_memory(memory_id: str) -> bool:
    store = _selected_store()
    deleted = store.delete_memory(memory_id=memory_id)
    log_event(
        "memory.store.delete",
        backend=store.health().get("backend"),
        memory_id=memory_id,
        deleted=deleted,
    )
    return deleted


def save_conversation_turn(
    *,
    session_id: str,
    user_message: str,
    assistant_message: str,
    trace_id: str | None = None,
    route: str | None = None,
    sources: list[dict[str, Any]] | None = None,
) -> str:
    store = _selected_store()
    turn_id = store.save_conversation_turn(
        session_id=session_id,
        user_message=user_message,
        assistant_message=assistant_message,
        trace_id=trace_id,
        route=route,
        sources=sources or [],
    )
    log_event(
        "conversation.store.save",
        backend=store.health().get("backend"),
        session_id=session_id,
        turn_id=turn_id,
    )
    return turn_id


def get_recent_conversation_turns(
    session_id: str,
    limit: int = 6,
) -> list[dict[str, Any]]:
    return _selected_store().get_recent_conversation_turns(
        session_id=session_id,
        limit=limit,
    )


def format_conversation_context(session_id: str, limit: int = 6) -> str:
    turns = get_recent_conversation_turns(
        session_id=session_id,
        limit=limit,
    )
    if not turns:
        return "Sem histórico recente para esta sessão."

    lines: list[str] = []
    for index, turn in enumerate(turns, start=1):
        route = turn.get("route") or "unknown"
        lines.append(f"Turno {index} | rota={route}")
        lines.append(f"Usuário: {turn.get('user_message', '')}")
        lines.append(f"Assistente: {turn.get('assistant_message', '')}")

    return "\n".join(lines)


def list_conversation_turns(limit: int = 50) -> list[dict[str, Any]]:
    return _selected_store().list_conversation_turns(limit=limit)
