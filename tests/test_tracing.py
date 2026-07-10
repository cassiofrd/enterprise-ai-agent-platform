from __future__ import annotations

from shared import observability
from shared.request_context import request_context_scope
from shared.tracing import get_current_span, start_span


def test_nested_spans_share_trace_and_track_parent(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setattr(observability, "EVENT_LOG_PATH", tmp_path / "events.jsonl")
    monkeypatch.setattr(observability, "LOG_DIR", tmp_path)
    observability._IN_MEMORY_EVENTS.clear()

    with request_context_scope(
        trace_id="trace-otel",
        request_id="request-1",
    ):
        with start_span("parent") as parent:
            with start_span("child") as child:
                assert get_current_span() == child
                assert child.trace_id == "trace-otel"
                assert child.parent_span_id == parent.span_id

    completed = [
        event
        for event in observability.get_recent_events(limit=20)
        if event["event_type"] == "span.completed"
    ]
    assert len(completed) == 2
    assert {event["span_name"] for event in completed} == {"parent", "child"}
