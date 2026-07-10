from __future__ import annotations

from collections import defaultdict
from threading import RLock
from typing import Any, Mapping


def _label_key(name: str, labels: Mapping[str, Any] | None = None) -> str:
    if not labels:
        return name

    normalized = ",".join(
        f"{key}={labels[key]}"
        for key in sorted(labels)
        if labels[key] is not None
    )
    return f"{name}{{{normalized}}}" if normalized else name


class MetricsCollector:
    """Thread-safe local metrics collector with optional dimensions."""

    def __init__(self) -> None:
        self._lock = RLock()
        self._counters: dict[str, float] = defaultdict(float)
        self._latency_totals_ms: dict[str, float] = defaultdict(float)
        self._latency_counts: dict[str, int] = defaultdict(int)
        self._gauges: dict[str, float] = {}

    def increment(
        self,
        name: str,
        value: float = 1.0,
        labels: Mapping[str, Any] | None = None,
    ) -> None:
        key = _label_key(name, labels)
        with self._lock:
            self._counters[key] += float(value)

    def observe_latency(
        self,
        name: str,
        latency_ms: float,
        labels: Mapping[str, Any] | None = None,
    ) -> None:
        key = _label_key(name, labels)
        with self._lock:
            self._latency_totals_ms[key] += float(latency_ms)
            self._latency_counts[key] += 1

    def set_gauge(
        self,
        name: str,
        value: float,
        labels: Mapping[str, Any] | None = None,
    ) -> None:
        key = _label_key(name, labels)
        with self._lock:
            self._gauges[key] = float(value)

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            latencies: dict[str, dict[str, float | int | None]] = {}
            for name, count in self._latency_counts.items():
                total = self._latency_totals_ms[name]
                latencies[name] = {
                    "count": count,
                    "total_ms": round(total, 2),
                    "avg_ms": round(total / count, 2) if count else None,
                }

            return {
                "counters": dict(sorted(self._counters.items())),
                "latencies": dict(sorted(latencies.items())),
                "gauges": dict(sorted(self._gauges.items())),
            }

    def reset(self) -> None:
        with self._lock:
            self._counters.clear()
            self._latency_totals_ms.clear()
            self._latency_counts.clear()
            self._gauges.clear()


metrics_collector = MetricsCollector()
