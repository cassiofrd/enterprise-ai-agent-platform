from __future__ import annotations

import time
from dataclasses import dataclass
from threading import RLock
from typing import Optional

import redis

from shared.observability import log_event
from shared.settings import settings


@dataclass
class _MemoryCacheEntry:
    value: str
    expires_at: float | None


_MEMORY_CACHE: dict[str, _MemoryCacheEntry] = {}
_MEMORY_LOCK = RLock()
_REDIS_CLIENT: redis.Redis | None = None
_REDIS_CLIENT_INITIALIZED = False
_REDIS_LOCK = RLock()


def _namespaced_key(key: str) -> str:
    normalized_prefix = settings.cache_key_prefix.strip().strip(":")
    normalized_key = key.strip()
    return f"{normalized_prefix}:{normalized_key}" if normalized_prefix else normalized_key


def get_redis_url() -> str | None:
    return settings.redis_url


def _build_redis_client(redis_url: str) -> redis.Redis:
    return redis.from_url(
        redis_url,
        decode_responses=True,
        socket_connect_timeout=settings.redis_socket_connect_timeout_seconds,
        socket_timeout=settings.redis_socket_timeout_seconds,
        health_check_interval=30,
    )


def get_redis_client(force_refresh: bool = False) -> redis.Redis | None:
    """Return a reusable Redis client when Redis is configured and reachable."""

    global _REDIS_CLIENT, _REDIS_CLIENT_INITIALIZED

    with _REDIS_LOCK:
        if force_refresh:
            _REDIS_CLIENT = None
            _REDIS_CLIENT_INITIALIZED = False

        if _REDIS_CLIENT_INITIALIZED:
            return _REDIS_CLIENT

        _REDIS_CLIENT_INITIALIZED = True
        redis_url = get_redis_url()

        if not redis_url:
            _REDIS_CLIENT = None
            return None

        try:
            client = _build_redis_client(redis_url)
            client.ping()
            _REDIS_CLIENT = client
            log_event(
                "cache.redis.connected",
                backend="redis",
            )
        except Exception as exc:
            _REDIS_CLIENT = None
            log_event(
                "cache.redis.unavailable",
                backend="memory",
                error_type=type(exc).__name__,
                error_message=str(exc),
            )

        return _REDIS_CLIENT


def _memory_cache_get(key: str) -> Optional[str]:
    now = time.monotonic()

    with _MEMORY_LOCK:
        entry = _MEMORY_CACHE.get(key)
        if entry is None:
            return None

        if entry.expires_at is not None and entry.expires_at <= now:
            _MEMORY_CACHE.pop(key, None)
            return None

        return entry.value


def _memory_cache_set(key: str, value: str, ttl_seconds: int) -> None:
    expires_at = (
        time.monotonic() + ttl_seconds
        if ttl_seconds > 0
        else None
    )

    with _MEMORY_LOCK:
        _MEMORY_CACHE[key] = _MemoryCacheEntry(
            value=value,
            expires_at=expires_at,
        )


def cache_get(key: str) -> Optional[str]:
    namespaced_key = _namespaced_key(key)
    client = get_redis_client()

    if client is not None:
        try:
            value = client.get(namespaced_key)
            if value is not None:
                log_event(
                    "cache.hit",
                    cache_key=namespaced_key,
                    backend="redis",
                )
                return value

            log_event(
                "cache.miss",
                cache_key=namespaced_key,
                backend="redis",
            )
        except Exception as exc:
            log_event(
                "cache.redis.error",
                operation="get",
                cache_key=namespaced_key,
                error_type=type(exc).__name__,
                error_message=str(exc),
                fallback="memory",
            )

    value = _memory_cache_get(namespaced_key)

    log_event(
        "cache.hit" if value is not None else "cache.miss",
        cache_key=namespaced_key,
        backend="memory",
    )
    return value


def cache_set(
    key: str,
    value: str,
    ttl_seconds: int | None = None,
) -> None:
    namespaced_key = _namespaced_key(key)
    effective_ttl = (
        settings.cache_default_ttl_seconds
        if ttl_seconds is None
        else ttl_seconds
    )

    if effective_ttl < 0:
        raise ValueError("ttl_seconds must be greater than or equal to 0.")

    client = get_redis_client()

    if client is not None:
        try:
            if effective_ttl == 0:
                client.set(namespaced_key, value)
            else:
                client.setex(namespaced_key, effective_ttl, value)

            log_event(
                "cache.store",
                cache_key=namespaced_key,
                backend="redis",
                ttl_seconds=effective_ttl,
            )
            return
        except Exception as exc:
            log_event(
                "cache.redis.error",
                operation="set",
                cache_key=namespaced_key,
                error_type=type(exc).__name__,
                error_message=str(exc),
                fallback="memory",
            )

    _memory_cache_set(
        namespaced_key,
        value,
        ttl_seconds=effective_ttl,
    )

    log_event(
        "cache.store",
        cache_key=namespaced_key,
        backend="memory",
        ttl_seconds=effective_ttl,
    )


def cache_delete(key: str) -> bool:
    namespaced_key = _namespaced_key(key)
    deleted = False
    client = get_redis_client()

    if client is not None:
        try:
            deleted = bool(client.delete(namespaced_key)) or deleted
        except Exception as exc:
            log_event(
                "cache.redis.error",
                operation="delete",
                cache_key=namespaced_key,
                error_type=type(exc).__name__,
                error_message=str(exc),
                fallback="memory",
            )

    with _MEMORY_LOCK:
        deleted = (_MEMORY_CACHE.pop(namespaced_key, None) is not None) or deleted

    log_event(
        "cache.delete",
        cache_key=namespaced_key,
        deleted=deleted,
    )
    return deleted


def cache_backend() -> str:
    return "redis" if get_redis_client() is not None else "memory"


def cache_health() -> dict:
    client = get_redis_client()

    if client is None:
        return {
            "backend": "memory",
            "redis_configured": bool(get_redis_url()),
            "redis_available": False,
            "memory_cache_size": memory_cache_size(),
            "key_prefix": settings.cache_key_prefix,
            "default_ttl_seconds": settings.cache_default_ttl_seconds,
        }

    try:
        client.ping()
        redis_available = True
        error = None
    except Exception as exc:
        redis_available = False
        error = f"{type(exc).__name__}: {exc}"

    return {
        "backend": "redis" if redis_available else "memory",
        "redis_configured": True,
        "redis_available": redis_available,
        "memory_cache_size": memory_cache_size(),
        "key_prefix": settings.cache_key_prefix,
        "default_ttl_seconds": settings.cache_default_ttl_seconds,
        "error": error,
    }


def memory_cache_size() -> int:
    now = time.monotonic()

    with _MEMORY_LOCK:
        expired_keys = [
            key
            for key, entry in _MEMORY_CACHE.items()
            if entry.expires_at is not None and entry.expires_at <= now
        ]
        for key in expired_keys:
            _MEMORY_CACHE.pop(key, None)

        return len(_MEMORY_CACHE)


def clear_memory_cache() -> None:
    """Clear only the in-process fallback cache. Intended for tests and maintenance."""

    with _MEMORY_LOCK:
        _MEMORY_CACHE.clear()


def reset_redis_client() -> None:
    """Forget the cached Redis client so availability can be checked again."""

    global _REDIS_CLIENT, _REDIS_CLIENT_INITIALIZED

    with _REDIS_LOCK:
        _REDIS_CLIENT = None
        _REDIS_CLIENT_INITIALIZED = False
