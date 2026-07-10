from __future__ import annotations

import json
import time
import uuid
from collections import deque
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from functools import wraps
from typing import Any, Callable, Iterator, TypeVar

from shared.config import EVENT_LOG_PATH, LOG_DIR
from shared.metrics import metrics_collector
from shared.request_context import get_request_context


_F = TypeVar("_F", bound=Callable[..., Any])

MODEL_PRICING_USD_PER_1M_TOKENS = {
    "gpt-4o": {"input": 2.50, "output": 10.00},
}

_IN_MEMORY_EVENTS: list[dict[str, Any]] = []


def new_trace_id() -> str:
    return str(uuid.uuid4())


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def estimate_llm_cost_usd(model: str, input_tokens: int | None, output_tokens: int | None) -> dict[str, float | None]:
    pricing = MODEL_PRICING_USD_PER_1M_TOKENS.get(model)

    if not pricing or input_tokens is None or output_tokens is None:
        return {
            "estimated_input_cost_usd": None,
            "estimated_output_cost_usd": None,
            "estimated_total_cost_usd": None,
        }

    input_cost = (input_tokens / 1_000_000) * pricing["input"]
    output_cost = (output_tokens / 1_000_000) * pricing["output"]

    return {
        "estimated_input_cost_usd": round(input_cost, 8),
        "estimated_output_cost_usd": round(output_cost, 8),
        "estimated_total_cost_usd": round(input_cost + output_cost, 8),
    }


def log_event(event_type: str, **fields: Any) -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    context = get_request_context()
    context_fields = context.to_dict() if context is not None else {}
    payload = {"ts": time.time(), "timestamp": now_iso(), "event_type": event_type, **context_fields, **fields}
    _IN_MEMORY_EVENTS.append(payload)
    if len(_IN_MEMORY_EVENTS) > 1000:
        del _IN_MEMORY_EVENTS[: len(_IN_MEMORY_EVENTS) - 1000]
    with EVENT_LOG_PATH.open("a", encoding="utf-8") as f:
        f.write(json.dumps(payload, ensure_ascii=False, default=str) + "\n")
    metrics_collector.increment("events.total")
    metrics_collector.increment(f"events.{event_type}")
    status = payload.get("status")
    if status: metrics_collector.increment(f"status.{status}")
    latency_ms = payload.get("latency_ms")
    if latency_ms is not None: metrics_collector.observe_latency(event_type, float(latency_ms))
    print(f"[OBS] {event_type} | {fields}")


@contextmanager
def observe_duration(event_type: str, **fields: Any) -> Iterator[None]:
    start = time.perf_counter()

    try:
        yield
        latency_ms = round((time.perf_counter() - start) * 1000, 2)
        log_event(event_type, status="success", latency_ms=latency_ms, **fields)
    except Exception as exc:
        latency_ms = round((time.perf_counter() - start) * 1000, 2)
        log_event(
            event_type,
            status="error",
            latency_ms=latency_ms,
            error_type=type(exc).__name__,
            error_message=str(exc),
            **fields,
        )
        raise


def measure_time(event_type: str, **static_fields: Any):
    def decorator(func: _F) -> _F:
        @wraps(func)
        def wrapper(*args: Any, **kwargs: Any):
            with observe_duration(event_type, function=func.__qualname__, **static_fields):
                return func(*args, **kwargs)
        return wrapper  # type: ignore[return-value]
    return decorator


def log_llm_usage(event_type: str, response: Any, trace_id: str, agent: str, model: str) -> None:
    usage = getattr(response, "usage_metadata", None) or {}

    input_tokens = usage.get("input_tokens")
    output_tokens = usage.get("output_tokens")
    total_tokens = usage.get("total_tokens")

    cost = estimate_llm_cost_usd(
        model=model,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
    )

    log_event(
        event_type,
        trace_id=trace_id,
        agent=agent,
        model=model,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        total_tokens=total_tokens,
        **cost,
    )


def _safe_event_key(event: dict[str, Any]) -> tuple:
    """Build a best-effort key to deduplicate events from memory and JSONL."""
    return (
        event.get("timestamp"),
        event.get("event_type"),
        event.get("trace_id"),
        event.get("agent"),
        event.get("target"),
        event.get("latency_ms"),
    )


def _load_recent_events_from_file(limit: int = 1000, path: Path = EVENT_LOG_PATH) -> list[dict[str, Any]]:
    """Read recent JSONL events from disk without loading very large files fully into memory."""
    if limit <= 0 or not path.exists():
        return []

    lines: deque[str] = deque(maxlen=limit)
    try:
        with path.open("r", encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    lines.append(line)
    except OSError:
        return []

    events: list[dict[str, Any]] = []
    for line in lines:
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            events.append(payload)
    return events


def _combined_events(limit: int = 1000) -> list[dict[str, Any]]:
    disk_events = _load_recent_events_from_file(limit=limit)
    memory_events = _IN_MEMORY_EVENTS[-limit:]

    deduped: dict[tuple, dict[str, Any]] = {}
    for event in [*disk_events, *memory_events]:
        deduped[_safe_event_key(event)] = event

    events = sorted(deduped.values(), key=lambda item: float(item.get("ts") or 0))
    return events[-limit:]


def get_recent_events(limit: int = 200) -> list[dict[str, Any]]:
    return _combined_events(limit=limit)


def _events_for_summary(limit: int = 1000) -> list[dict[str, Any]]:
    return _combined_events(limit=limit)


def get_metrics_summary() -> dict[str, Any]:
    events = _events_for_summary()

    total_events = len(events)
    total_tokens = sum(e.get("total_tokens") or 0 for e in events)
    total_cost = sum(e.get("estimated_total_cost_usd") or 0 for e in events)

    cache_hits = sum(1 for e in events if e.get("event_type") in {"rag.cache.hit", "cache.hit"})
    cache_misses = sum(1 for e in events if e.get("event_type") in {"rag.cache.miss", "cache.miss"})
    cache_total = cache_hits + cache_misses
    cache_hit_rate = round((cache_hits / cache_total) * 100, 2) if cache_total else None

    latency_events = [e for e in events if e.get("latency_ms") is not None]
    avg_latency_ms = (
        round(sum(float(e["latency_ms"]) for e in latency_events) / len(latency_events), 2)
        if latency_events
        else None
    )

    trace_ids = sorted({e.get("trace_id") for e in events if e.get("trace_id")})

    return {
        "collector": metrics_collector.snapshot(),
        "total_events": total_events,
        "total_traces": len(trace_ids),
        "total_tokens": total_tokens,
        "estimated_total_cost_usd": round(total_cost, 8),
        "cache_hits": cache_hits,
        "cache_misses": cache_misses,
        "cache_hit_rate_percent": cache_hit_rate,
        "avg_latency_ms": avg_latency_ms,
        "recent_trace_ids": trace_ids[-10:],
    }


def get_trace_events(trace_id: str, limit: int = 500) -> list[dict[str, Any]]:
    if not trace_id:
        return []
    events = [e for e in _combined_events(limit=2000) if e.get("trace_id") == trace_id]
    events = sorted(events, key=lambda item: float(item.get("ts") or 0))
    return events[-limit:]


def get_trace_summary(trace_id: str) -> dict[str, Any]:
    events = get_trace_events(trace_id=trace_id, limit=1000)
    latency_events = [e for e in events if e.get("latency_ms") is not None]
    llm_events = [e for e in events if e.get("total_tokens") is not None or "llm" in str(e.get("event_type", ""))]
    route_events = [e for e in events if e.get("route") or e.get("selected_route")]
    source_events = [e for e in events if e.get("sources")]

    total_latency_ms = round(sum(float(e.get("latency_ms") or 0) for e in latency_events), 2)
    total_tokens = sum(e.get("total_tokens") or 0 for e in events)
    total_cost = round(sum(e.get("estimated_total_cost_usd") or 0 for e in events), 8)
    statuses = sorted({str(e.get("status")) for e in events if e.get("status")})
    agents = sorted({str(e.get("agent")) for e in events if e.get("agent")})
    targets = sorted({str(e.get("target") or e.get("target_agent")) for e in events if e.get("target") or e.get("target_agent")})

    steps = []
    for event in latency_events:
        steps.append(
            {
                "timestamp": event.get("timestamp"),
                "event_type": event.get("event_type"),
                "agent": event.get("agent"),
                "target_agent": event.get("target_agent") or event.get("target"),
                "status": event.get("status"),
                "latency_ms": event.get("latency_ms"),
                "tool_operation": event.get("tool_operation"),
                "url": event.get("url"),
            }
        )

    first_event = events[0] if events else {}
    last_event = events[-1] if events else {}

    return {
        "trace_id": trace_id,
        "event_count": len(events),
        "first_timestamp": first_event.get("timestamp"),
        "last_timestamp": last_event.get("timestamp"),
        "agents": agents,
        "targets": targets,
        "statuses": statuses,
        "routes": [e.get("route") or e.get("selected_route") for e in route_events if e.get("route") or e.get("selected_route")],
        "total_observed_latency_ms": total_latency_ms,
        "latency_event_count": len(latency_events),
        "total_tokens": total_tokens,
        "estimated_total_cost_usd": total_cost,
        "llm_event_count": len(llm_events),
        "source_event_count": len(source_events),
        "steps": steps,
    }


def get_trace_index(limit: int = 50) -> list[dict[str, Any]]:
    events = _combined_events(limit=2000)
    by_trace: dict[str, list[dict[str, Any]]] = {}
    for event in events:
        trace_id = event.get("trace_id")
        if trace_id:
            by_trace.setdefault(str(trace_id), []).append(event)

    rows: list[dict[str, Any]] = []
    for trace_id, trace_events in by_trace.items():
        trace_events = sorted(trace_events, key=lambda item: float(item.get("ts") or 0))
        latency_ms = sum(float(e.get("latency_ms") or 0) for e in trace_events if e.get("latency_ms") is not None)
        total_tokens = sum(e.get("total_tokens") or 0 for e in trace_events)
        total_cost = sum(e.get("estimated_total_cost_usd") or 0 for e in trace_events)
        routes = [e.get("route") or e.get("selected_route") for e in trace_events if e.get("route") or e.get("selected_route")]
        rows.append(
            {
                "trace_id": trace_id,
                "first_timestamp": trace_events[0].get("timestamp"),
                "last_timestamp": trace_events[-1].get("timestamp"),
                "event_count": len(trace_events),
                "total_observed_latency_ms": round(latency_ms, 2),
                "total_tokens": total_tokens,
                "estimated_total_cost_usd": round(total_cost, 8),
                "last_route": routes[-1] if routes else None,
            }
        )

    rows = sorted(rows, key=lambda item: str(item.get("last_timestamp") or ""), reverse=True)
    return rows[:limit]
