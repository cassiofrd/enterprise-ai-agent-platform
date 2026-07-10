from __future__ import annotations

from shared.request_context import (
    bind_request_context,
    get_request_context,
    request_context_scope,
)


def test_bind_request_context_preserves_ids_and_adds_session():
    with request_context_scope(
        trace_id="trace-1",
        request_id="request-1",
        endpoint="/chat",
    ):
        with bind_request_context(session_id="session-1") as enriched:
            assert enriched.trace_id == "trace-1"
            assert enriched.request_id == "request-1"
            assert enriched.session_id == "session-1"

        restored = get_request_context()
        assert restored is not None
        assert restored.session_id is None
