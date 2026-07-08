from __future__ import annotations

from shared import observability


def test_trace_summary_groups_events_by_trace_id(tmp_path, monkeypatch):
    log_path = tmp_path / "agent_events.jsonl"
    monkeypatch.setattr(observability, "EVENT_LOG_PATH", log_path)
    monkeypatch.setattr(observability, "LOG_DIR", tmp_path)
    observability._IN_MEMORY_EVENTS.clear()

    trace_id = observability.new_trace_id()
    observability.log_event("supervisor.route.selected", trace_id=trace_id, route="knowledge")
    observability.log_event("azure_search.query", trace_id=trace_id, agent="supervisor", status="success", latency_ms=42.5)
    observability.log_event("llm.supervisor.usage", trace_id=trace_id, agent="supervisor", total_tokens=10, estimated_total_cost_usd=0.001)
    observability.log_event("unrelated.event", trace_id="other-trace")

    summary = observability.get_trace_summary(trace_id)

    assert summary["trace_id"] == trace_id
    assert summary["event_count"] == 3
    assert summary["total_observed_latency_ms"] == 42.5
    assert summary["total_tokens"] == 10
    assert summary["routes"] == ["knowledge"]
    assert summary["steps"][0]["event_type"] == "azure_search.query"


def test_trace_index_returns_recent_traces(tmp_path, monkeypatch):
    log_path = tmp_path / "agent_events.jsonl"
    monkeypatch.setattr(observability, "EVENT_LOG_PATH", log_path)
    monkeypatch.setattr(observability, "LOG_DIR", tmp_path)
    observability._IN_MEMORY_EVENTS.clear()

    observability.log_event("event.a", trace_id="trace-a", latency_ms=10)
    observability.log_event("event.b", trace_id="trace-b", selected_route="inventory")

    rows = observability.get_trace_index(limit=10)
    trace_ids = {row["trace_id"] for row in rows}

    assert {"trace-a", "trace-b"}.issubset(trace_ids)
    assert any(row["last_route"] == "inventory" for row in rows)
