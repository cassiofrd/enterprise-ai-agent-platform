from __future__ import annotations

import json
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Iterator

from shared.config import EVENT_LOG_PATH, LOG_DIR


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

    payload = {
        "ts": time.time(),
        "timestamp": now_iso(),
        "event_type": event_type,
        **fields,
    }

    _IN_MEMORY_EVENTS.append(payload)

    if len(_IN_MEMORY_EVENTS) > 1000:
        del _IN_MEMORY_EVENTS[: len(_IN_MEMORY_EVENTS) - 1000]

    with EVENT_LOG_PATH.open("a", encoding="utf-8") as f:
        f.write(json.dumps(payload, ensure_ascii=False, default=str) + "\n")

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


def get_recent_events(limit: int = 200) -> list[dict[str, Any]]:
    return _IN_MEMORY_EVENTS[-limit:]


def get_metrics_summary() -> dict[str, Any]:
    events = _IN_MEMORY_EVENTS

    total_events = len(events)
    total_tokens = sum(e.get("total_tokens") or 0 for e in events)
    total_cost = sum(e.get("estimated_total_cost_usd") or 0 for e in events)

    cache_hits = sum(1 for e in events if e.get("event_type") == "rag.cache.hit")
    cache_misses = sum(1 for e in events if e.get("event_type") == "rag.cache.miss")
    cache_total = cache_hits + cache_misses
    cache_hit_rate = round((cache_hits / cache_total) * 100, 2) if cache_total else None

    latency_events = [e for e in events if e.get("latency_ms") is not None]
    avg_latency_ms = (
        round(sum(e["latency_ms"] for e in latency_events) / len(latency_events), 2)
        if latency_events
        else None
    )

    trace_ids = sorted({e.get("trace_id") for e in events if e.get("trace_id")})

    return {
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