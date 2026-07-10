from __future__ import annotations

from shared import observability
from shared.metrics import metrics_collector


def _reset_observability_state() -> None:
    """Keep global observability singletons isolated between tests."""
    observability._IN_MEMORY_EVENTS.clear()
    metrics_collector.reset()
    tracker = getattr(observability, "cost_tracker", None)
    if tracker is not None and hasattr(tracker, "reset"):
        tracker.reset()


def test_cost_summary_aggregates_by_agent_session_trace_and_model(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setattr(
        observability,
        "EVENT_LOG_PATH",
        tmp_path / "events.jsonl",
    )
    monkeypatch.setattr(
        observability,
        "LOG_DIR",
        tmp_path,
    )
    monkeypatch.setattr(
        observability,
        "_load_recent_events_from_file",
        lambda limit=1000, path=None: [],
    )
    _reset_observability_state()

    observability.log_event(
        "llm.supervisor.usage",
        trace_id="trace-1",
        session_id="session-1",
        agent="supervisor",
        model="gpt-4o-mini",
        input_tokens=100,
        output_tokens=50,
        total_tokens=150,
        estimated_total_cost_usd=0.000045,
    )
    observability.log_event(
        "llm.validator.usage",
        trace_id="trace-1",
        session_id="session-1",
        agent="validator",
        model="gpt-4o-mini",
        input_tokens=20,
        output_tokens=10,
        total_tokens=30,
        estimated_total_cost_usd=0.000009,
    )

    summary = observability.get_cost_summary()

    assert summary["total"]["llm_calls"] == 2
    assert summary["total"]["input_tokens"] == 120
    assert summary["total"]["output_tokens"] == 60
    assert summary["total"]["total_tokens"] == 180
    assert summary["total"]["estimated_total_cost_usd"] == 0.000054

    assert summary["by_agent"]["supervisor"]["llm_calls"] == 1
    assert summary["by_agent"]["validator"]["llm_calls"] == 1
    assert summary["by_session"]["session-1"]["llm_calls"] == 2
    assert summary["by_trace"]["trace-1"]["llm_calls"] == 2
    assert summary["by_model"]["gpt-4o-mini"]["llm_calls"] == 2


def test_agent_metrics_include_retries_errors_and_circuit_breaker(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setattr(
        observability,
        "EVENT_LOG_PATH",
        tmp_path / "events.jsonl",
    )
    monkeypatch.setattr(
        observability,
        "LOG_DIR",
        tmp_path,
    )
    monkeypatch.setattr(
        observability,
        "_load_recent_events_from_file",
        lambda limit=1000, path=None: [],
    )
    _reset_observability_state()

    observability.log_event(
        "a2a.inventory.retry_scheduled",
        target_agent="inventory",
    )
    observability.log_event(
        "circuit_breaker.opened",
        target_agent="inventory",
    )
    observability.log_event(
        "a2a.inventory.error",
        target_agent="inventory",
        status="error",
        latency_ms=25,
    )

    summary = observability.get_agent_metrics_summary()

    assert summary["inventory"]["retries"] == 1
    assert summary["inventory"]["circuit_breaker_opens"] == 1
    assert summary["inventory"]["errors"] == 1
    assert summary["inventory"]["avg_latency_ms"] == 25
