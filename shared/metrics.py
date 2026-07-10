from __future__ import annotations

from collections import defaultdict
from threading import RLock
from typing import Any


class MetricsCollector:
    def __init__(self) -> None:
        self._lock = RLock()
        self._counters: dict[str, float] = defaultdict(float)
        self._latency_totals_ms: dict[str, float] = defaultdict(float)
        self._latency_counts: dict[str, int] = defaultdict(int)

    def increment(self, name: str, value: float = 1.0) -> None:
        with self._lock:
            self._counters[name] += value

    def observe_latency(self, name: str, latency_ms: float) -> None:
        with self._lock:
            self._latency_totals_ms[name] += float(latency_ms)
            self._latency_counts[name] += 1

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            latencies = {}
            for name, count in self._latency_counts.items():
                total = self._latency_totals_ms[name]
                latencies[name] = {"count": count, "total_ms": round(total, 2), "avg_ms": round(total / count, 2) if count else None}
            return {"counters": dict(sorted(self._counters.items())), "latencies": dict(sorted(latencies.items()))}

    def reset(self) -> None:
        with self._lock:
            self._counters.clear(); self._latency_totals_ms.clear(); self._latency_counts.clear()


metrics_collector = MetricsCollector()
