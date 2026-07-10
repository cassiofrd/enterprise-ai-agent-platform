from __future__ import annotations

import fnmatch

from shared.redis_memory_store import RedisMemoryStore


class FakeRedis:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}
        self.sorted_sets: dict[str, dict[str, float]] = {}
        self.expirations: dict[str, int] = {}

    def ping(self):
        return True

    def set(self, key: str, value: str):
        self.values[key] = value
        return True

    def setex(self, key: str, ttl: int, value: str):
        self.values[key] = value
        self.expirations[key] = ttl
        return True

    def get(self, key: str):
        return self.values.get(key)

    def delete(self, key: str):
        return 1 if self.values.pop(key, None) is not None else 0

    def zadd(self, key: str, mapping: dict[str, float]):
        self.sorted_sets.setdefault(key, {}).update(mapping)
        return len(mapping)

    def zrevrange(self, key: str, start: int, end: int):
        members = sorted(
            self.sorted_sets.get(key, {}).items(),
            key=lambda item: item[1],
            reverse=True,
        )
        values = [member for member, _score in members]
        return values[start : end + 1]

    def zrem(self, key: str, member: str):
        return int(self.sorted_sets.get(key, {}).pop(member, None) is not None)

    def expire(self, key: str, ttl: int):
        self.expirations[key] = ttl
        return True


def test_redis_store_saves_searches_and_deletes_long_term_memory():
    client = FakeRedis()
    store = RedisMemoryStore(
        client=client,
        key_prefix="test-memory",
        conversation_ttl_seconds=60,
        long_term_ttl_seconds=0,
    )

    memory_id = store.save_memory(
        key="inventory:supplier:PARAFUSO-M20",
        value="XYZ Metais",
        memory_type="supplier",
        source_agent="inventory",
    )

    results = store.search_memories(query="PARAFUSO-M20", limit=10)
    assert len(results) == 1
    assert results[0]["id"] == memory_id
    assert results[0]["value"] == "XYZ Metais"

    assert store.delete_memory(memory_id=memory_id) is True
    assert store.search_memories(query="PARAFUSO-M20", limit=10) == []


def test_redis_store_preserves_conversation_order_and_ttl():
    client = FakeRedis()
    store = RedisMemoryStore(
        client=client,
        key_prefix="test-memory",
        conversation_ttl_seconds=120,
        long_term_ttl_seconds=0,
    )

    first_id = store.save_conversation_turn(
        session_id="session-1",
        trace_id="trace-1",
        user_message="Primeira pergunta",
        assistant_message="Primeira resposta",
        route="inventory",
        sources=[],
    )
    second_id = store.save_conversation_turn(
        session_id="session-1",
        trace_id="trace-2",
        user_message="Segunda pergunta",
        assistant_message="Segunda resposta",
        route="supplier",
        sources=[],
    )

    turns = store.get_recent_conversation_turns(
        session_id="session-1",
        limit=10,
    )

    assert [turn["id"] for turn in turns] == [first_id, second_id]
    assert turns[1]["route"] == "supplier"

    record_key = f"test-memory:conversation:record:{first_id}"
    assert client.expirations[record_key] == 120


def test_redis_store_health_reports_backend():
    store = RedisMemoryStore(
        client=FakeRedis(),
        key_prefix="test-memory",
    )

    health = store.health()

    assert health["backend"] == "redis"
    assert health["available"] is True
