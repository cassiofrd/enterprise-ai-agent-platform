from __future__ import annotations

import os
from typing import Optional

import redis


_MEMORY_CACHE: dict[str, str] = {}


def get_redis_url() -> str | None:
    return os.getenv("REDIS_URL")


def get_redis_client() -> redis.Redis | None:
    redis_url = get_redis_url()

    if not redis_url:
        return None

    try:
        client = redis.from_url(redis_url, decode_responses=True)
        client.ping()
        return client
    except Exception:
        return None


def cache_get(key: str) -> Optional[str]:
    client = get_redis_client()

    if client is not None:
        value = client.get(key)
        if value is not None:
            return value

    return _MEMORY_CACHE.get(key)


def cache_set(key: str, value: str, ttl_seconds: int = 3600) -> None:
    client = get_redis_client()

    if client is not None:
        client.setex(key, ttl_seconds, value)
        return

    _MEMORY_CACHE[key] = value


def cache_backend() -> str:
    client = get_redis_client()

    if client is not None:
        return "redis"

    return "memory"


def memory_cache_size() -> int:
    return len(_MEMORY_CACHE)