from shared import observability
from shared.metrics import metrics_collector
from shared.request_context import request_context_scope

def test_log_event_includes_request_context(tmp_path, monkeypatch):
    event_path = tmp_path / "events.jsonl"
    monkeypatch.setattr(observability, "EVENT_LOG_PATH", event_path)
    monkeypatch.setattr(observability, "LOG_DIR", tmp_path)
    metrics_collector.reset(); observability._IN_MEMORY_EVENTS.clear()
    with request_context_scope(trace_id="trace-test", request_id="request-test", session_id="session-test", endpoint="/test"):
        observability.log_event("test.event", status="success", latency_ms=12.5)
    event = observability.get_recent_events(limit=1)[0]
    assert event["trace_id"] == "trace-test"
    assert event["request_id"] == "request-test"
    assert event["session_id"] == "session-test"
    assert event["endpoint"] == "/test"
    snapshot = metrics_collector.snapshot()
    assert snapshot["counters"]["events.total"] == 1
    assert snapshot["counters"]["events.test.event"] == 1
    assert snapshot["latencies"]["test.event"]["avg_ms"] == 12.5
