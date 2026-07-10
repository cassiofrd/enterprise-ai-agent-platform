from __future__ import annotations

import time

from shared import cache


class FakeRedis:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}
        self.ttls: dict[str, int] = {}
        self.ping_calls = 0

    def ping(self):
        self.ping_calls += 1
        return True

    def get(self, key: str):
        return self.values.get(key)

    def setex(self, key: str, ttl: int, value: str):
        self.values[key] = value
        self.ttls[key] = ttl
        return True

    def set(self, key: str, value: str):
        self.values[key] = value
        return True

    def delete(self, key: str):
        return 1 if self.values.pop(key, None) is not None else 0


class BrokenRedis:
    def get(self, key: str):
        raise ConnectionError("redis unavailable")

    def setex(self, key: str, ttl: int, value: str):
        raise ConnectionError("redis unavailable")

    def set(self, key: str, value: str):
        raise ConnectionError("redis unavailable")

    def delete(self, key: str):
        raise ConnectionError("redis unavailable")


def setup_function():
    cache.clear_memory_cache()
    cache.reset_redis_client()


def test_memory_cache_round_trip_when_redis_is_disabled(monkeypatch):
    monkeypatch.setattr(cache, "get_redis_url", lambda: None)

    cache.cache_set("rag:test", "value", ttl_seconds=60)

    assert cache.cache_backend() == "memory"
    assert cache.cache_get("rag:test") == "value"
    assert cache.memory_cache_size() == 1


def test_memory_cache_entry_expires(monkeypatch):
    monkeypatch.setattr(cache, "get_redis_url", lambda: None)

    cache.cache_set("short-lived", "value", ttl_seconds=1)
    assert cache.cache_get("short-lived") == "value"

    namespaced_key = cache._namespaced_key("short-lived")
    cache._MEMORY_CACHE[namespaced_key].expires_at = time.monotonic() - 1

    assert cache.cache_get("short-lived") is None
    assert cache.memory_cache_size() == 0


def test_redis_backend_uses_namespaced_key_and_ttl(monkeypatch):
    fake = FakeRedis()
    monkeypatch.setattr(cache, "get_redis_client", lambda force_refresh=False: fake)

    cache.cache_set("policy", "cached", ttl_seconds=120)

    expected_key = f"{cache.settings.cache_key_prefix}:policy"
    assert fake.values[expected_key] == "cached"
    assert fake.ttls[expected_key] == 120
    assert cache.cache_get("policy") == "cached"


def test_redis_failure_falls_back_to_memory(monkeypatch):
    broken = BrokenRedis()
    monkeypatch.setattr(cache, "get_redis_client", lambda force_refresh=False: broken)

    cache.cache_set("fallback", "memory-value", ttl_seconds=60)

    assert cache.cache_get("fallback") == "memory-value"
    assert cache.memory_cache_size() == 1


def test_cache_delete_removes_memory_entry(monkeypatch):
    monkeypatch.setattr(cache, "get_redis_url", lambda: None)

    cache.cache_set("delete-me", "value", ttl_seconds=60)

    assert cache.cache_delete("delete-me") is True
    assert cache.cache_get("delete-me") is None
