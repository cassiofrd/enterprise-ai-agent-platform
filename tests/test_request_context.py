from shared.request_context import get_request_context, request_context_scope

def test_request_context_scope_sets_and_resets_context():
    assert get_request_context() is None
    with request_context_scope(trace_id="trace-1", request_id="request-1", session_id="session-1", endpoint="/copilot") as context:
        current = get_request_context()
        assert current == context
        assert current.trace_id == "trace-1"
        assert current.request_id == "request-1"
    assert get_request_context() is None
