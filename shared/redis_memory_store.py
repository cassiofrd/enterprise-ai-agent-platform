from __future__ import annotations

import json
import time
import uuid
from datetime import datetime, timezone
from threading import RLock
from typing import Any

from shared.memory_store import MemoryStore


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class RedisMemoryStore(MemoryStore):
    """Redis implementation for shared memory across application replicas."""

    def __init__(
        self,
        *,
        client,
        key_prefix: str,
        conversation_ttl_seconds: int = 604800,
        long_term_ttl_seconds: int = 0,
    ) -> None:
        self.client = client
        self.key_prefix = key_prefix.strip().strip(":")
        self.conversation_ttl_seconds = conversation_ttl_seconds
        self.long_term_ttl_seconds = long_term_ttl_seconds
        self._score_lock = RLock()
        self._last_score = 0.0

    def _key(self, suffix: str) -> str:
        return f"{self.key_prefix}:{suffix}" if self.key_prefix else suffix

    def _memory_record_key(self, memory_id: str) -> str:
        return self._key(f"memory:record:{memory_id}")

    def _conversation_record_key(self, turn_id: str) -> str:
        return self._key(f"conversation:record:{turn_id}")

    @property
    def _memory_index_key(self) -> str:
        return self._key("memory:index")

    @property
    def _conversation_index_key(self) -> str:
        return self._key("conversation:index")

    def _session_index_key(self, session_id: str) -> str:
        return self._key(f"conversation:session:{session_id}")

    def _timestamp(self) -> float:
        """Return a strictly increasing Redis sorted-set score.

        Consecutive writes can occur within the same clock tick. A monotonic
        increment avoids ambiguous ordering when Redis receives equal scores.
        """
        with self._score_lock:
            current = time.time()
            if current <= self._last_score:
                current = self._last_score + 0.000001
            self._last_score = current
            return current

    def _store_json(self, key: str, payload: dict[str, Any], ttl_seconds: int) -> None:
        encoded = json.dumps(payload, ensure_ascii=False)
        if ttl_seconds > 0:
            self.client.setex(key, ttl_seconds, encoded)
        else:
            self.client.set(key, encoded)

    def _read_json(self, key: str) -> dict[str, Any] | None:
        raw = self.client.get(key)
        if raw is None:
            return None
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8")
        return json.loads(raw)

    def save_memory(
        self,
        *,
        key: str,
        value: str,
        memory_type: str,
        source_agent: str,
    ) -> str:
        memory_id = str(uuid.uuid4())
        created_at = _now_iso()
        score = self._timestamp()
        payload = {
            "id": memory_id,
            "memory_type": memory_type,
            "key": key,
            "value": value,
            "source_agent": source_agent,
            "created_at": created_at,
        }

        record_key = self._memory_record_key(memory_id)
        self._store_json(
            record_key,
            payload,
            ttl_seconds=self.long_term_ttl_seconds,
        )
        self.client.zadd(self._memory_index_key, {memory_id: score})
        if self.long_term_ttl_seconds > 0:
            self.client.expire(self._memory_index_key, self.long_term_ttl_seconds)

        return memory_id

    def _memory_records(self, *, limit: int) -> list[dict[str, Any]]:
        ids = self.client.zrevrange(self._memory_index_key, 0, max(0, limit - 1))
        records: list[dict[str, Any]] = []

        for memory_id in ids:
            if isinstance(memory_id, bytes):
                memory_id = memory_id.decode("utf-8")
            item = self._read_json(self._memory_record_key(str(memory_id)))
            if item is not None:
                records.append(item)

        return records

    def search_memories(self, *, query: str, limit: int) -> list[dict[str, Any]]:
        normalized = query.casefold()
        scan_limit = max(limit * 10, 100)
        matches = [
            item
            for item in self._memory_records(limit=scan_limit)
            if normalized in str(item.get("key", "")).casefold()
            or normalized in str(item.get("value", "")).casefold()
        ]
        return matches[:limit]

    def list_memories(self, *, limit: int) -> list[dict[str, Any]]:
        return self._memory_records(limit=limit)

    def delete_memory(self, *, memory_id: str) -> bool:
        deleted = bool(self.client.delete(self._memory_record_key(memory_id)))
        self.client.zrem(self._memory_index_key, memory_id)
        return deleted

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
        turn_id = str(uuid.uuid4())
        created_at = _now_iso()
        score = self._timestamp()
        payload = {
            "id": turn_id,
            "session_id": session_id,
            "trace_id": trace_id,
            "user_message": user_message,
            "assistant_message": assistant_message,
            "route": route,
            "sources": sources,
            "created_at": created_at,
        }

        record_key = self._conversation_record_key(turn_id)
        self._store_json(
            record_key,
            payload,
            ttl_seconds=self.conversation_ttl_seconds,
        )
        self.client.zadd(self._conversation_index_key, {turn_id: score})
        self.client.zadd(self._session_index_key(session_id), {turn_id: score})

        if self.conversation_ttl_seconds > 0:
            self.client.expire(
                self._conversation_index_key,
                self.conversation_ttl_seconds,
            )
            self.client.expire(
                self._session_index_key(session_id),
                self.conversation_ttl_seconds,
            )

        return turn_id

    def _conversation_records(
        self,
        *,
        index_key: str,
        limit: int,
        chronological: bool,
    ) -> list[dict[str, Any]]:
        ids = self.client.zrevrange(index_key, 0, max(0, limit - 1))
        records: list[dict[str, Any]] = []

        for turn_id in ids:
            if isinstance(turn_id, bytes):
                turn_id = turn_id.decode("utf-8")
            item = self._read_json(self._conversation_record_key(str(turn_id)))
            if item is not None:
                records.append(item)

        if chronological:
            records.reverse()
        return records

    def get_recent_conversation_turns(
        self,
        *,
        session_id: str,
        limit: int,
    ) -> list[dict[str, Any]]:
        return self._conversation_records(
            index_key=self._session_index_key(session_id),
            limit=limit,
            chronological=True,
        )

    def list_conversation_turns(self, *, limit: int) -> list[dict[str, Any]]:
        return self._conversation_records(
            index_key=self._conversation_index_key,
            limit=limit,
            chronological=False,
        )

    def health(self) -> dict[str, Any]:
        try:
            self.client.ping()
            return {
                "backend": "redis",
                "available": True,
                "key_prefix": self.key_prefix,
                "conversation_ttl_seconds": self.conversation_ttl_seconds,
                "long_term_ttl_seconds": self.long_term_ttl_seconds,
            }
        except Exception as exc:
            return {
                "backend": "redis",
                "available": False,
                "key_prefix": self.key_prefix,
                "error": f"{type(exc).__name__}: {exc}",
            }
