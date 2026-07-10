from __future__ import annotations

import time
import uuid
from contextlib import contextmanager
from contextvars import ContextVar, Token
from dataclasses import dataclass, field
from typing import Any, Iterator

from shared.request_context import get_request_context


@dataclass
class Span:
    name: str
    trace_id: str
    span_id: str
    parent_span_id: str | None = None
    attributes: dict[str, Any] = field(default_factory=dict)
    started_at: float = field(default_factory=time.perf_counter)

    def set_attribute(self, key: str, value: Any) -> None:
        self.attributes[key] = value


_CURRENT_SPAN: ContextVar[Span | None] = ContextVar("current_span", default=None)


def get_current_span() -> Span | None:
    return _CURRENT_SPAN.get()


def _new_span_id() -> str:
    return uuid.uuid4().hex[:16]


@contextmanager
def start_span(
    name: str,
    *,
    trace_id: str | None = None,
    attributes: dict[str, Any] | None = None,
) -> Iterator[Span]:
    """Create a local span whose shape maps cleanly to OpenTelemetry spans."""

    request_context = get_request_context()
    parent = get_current_span()
    resolved_trace_id = (
        trace_id
        or (parent.trace_id if parent else None)
        or (request_context.trace_id if request_context else None)
        or str(uuid.uuid4())
    )

    span = Span(
        name=name,
        trace_id=resolved_trace_id,
        span_id=_new_span_id(),
        parent_span_id=parent.span_id if parent else None,
        attributes=dict(attributes or {}),
    )
    token: Token = _CURRENT_SPAN.set(span)

    # Import lazily to avoid a circular dependency at module import time.
    from shared.observability import log_event

    log_event(
        "span.started",
        trace_id=span.trace_id,
        span_name=span.name,
        span_id=span.span_id,
        parent_span_id=span.parent_span_id,
        span_attributes=span.attributes,
    )

    try:
        yield span
    except Exception as exc:
        latency_ms = round((time.perf_counter() - span.started_at) * 1000, 2)
        log_event(
            "span.completed",
            trace_id=span.trace_id,
            span_name=span.name,
            span_id=span.span_id,
            parent_span_id=span.parent_span_id,
            span_attributes=span.attributes,
            status="error",
            latency_ms=latency_ms,
            error_type=type(exc).__name__,
            error_message=str(exc),
        )
        raise
    else:
        latency_ms = round((time.perf_counter() - span.started_at) * 1000, 2)
        log_event(
            "span.completed",
            trace_id=span.trace_id,
            span_name=span.name,
            span_id=span.span_id,
            parent_span_id=span.parent_span_id,
            span_attributes=span.attributes,
            status="success",
            latency_ms=latency_ms,
        )
    finally:
        _CURRENT_SPAN.reset(token)
