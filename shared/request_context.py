from __future__ import annotations

import time
import uuid
from contextlib import contextmanager
from contextvars import ContextVar, Token
from dataclasses import asdict, dataclass
from typing import Iterator


@dataclass(frozen=True)
class RequestContext:
    trace_id: str
    request_id: str
    session_id: str | None = None
    endpoint: str | None = None
    started_at_monotonic: float = 0.0

    def to_dict(self) -> dict:
        data = asdict(self)
        data.pop("started_at_monotonic", None)
        return {key: value for key, value in data.items() if value is not None}


_CURRENT_REQUEST_CONTEXT: ContextVar[RequestContext | None] = ContextVar(
    "current_request_context", default=None
)


def new_request_id() -> str:
    return str(uuid.uuid4())


def get_request_context() -> RequestContext | None:
    return _CURRENT_REQUEST_CONTEXT.get()


def set_request_context(context: RequestContext) -> Token:
    return _CURRENT_REQUEST_CONTEXT.set(context)


def reset_request_context(token: Token) -> None:
    _CURRENT_REQUEST_CONTEXT.reset(token)


@contextmanager
def request_context_scope(*, trace_id: str, request_id: str | None = None, session_id: str | None = None, endpoint: str | None = None) -> Iterator[RequestContext]:
    context = RequestContext(trace_id=trace_id, request_id=request_id or new_request_id(), session_id=session_id, endpoint=endpoint, started_at_monotonic=time.perf_counter())
    token = set_request_context(context)
    try:
        yield context
    finally:
        reset_request_context(token)
