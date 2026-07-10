from __future__ import annotations

import logging
from contextlib import contextmanager
from threading import RLock
from typing import Any, Iterator

from shared.request_context import get_request_context
from shared.settings import settings


LOGGER = logging.getLogger(__name__)

_INITIALIZATION_LOCK = RLock()
_INITIALIZED = False
_INITIALIZATION_ERROR: str | None = None
_PROVIDER = "disabled"
_EXPORTER = "none"


def _safe_attribute_value(value: Any) -> str | bool | int | float:
    if isinstance(value, (str, bool, int, float)):
        return value
    if value is None:
        return ""
    return str(value)


def initialize_telemetry(force: bool = False) -> dict[str, Any]:
    """Initialize OpenTelemetry and Azure Monitor once per process.

    The function is intentionally fail-open. Local development and tests keep
    working even when OpenTelemetry packages or an Application Insights
    connection string are unavailable.
    """

    global _INITIALIZED, _INITIALIZATION_ERROR, _PROVIDER, _EXPORTER

    with _INITIALIZATION_LOCK:
        if _INITIALIZED and not force:
            return telemetry_status()

        _INITIALIZED = True
        _INITIALIZATION_ERROR = None
        _PROVIDER = "disabled"
        _EXPORTER = "none"

        if not getattr(settings, "otel_enabled", False):
            return telemetry_status()

        try:
            connection_string = getattr(
                settings,
                "applicationinsights_connection_string",
                None,
            )

            if connection_string:
                from azure.monitor.opentelemetry import configure_azure_monitor

                configure_azure_monitor(
                    connection_string=connection_string,
                    service_name=getattr(
                        settings,
                        "otel_service_name",
                        "enterprise-ai-agent-supervisor",
                    ),
                )
                _PROVIDER = "azure-monitor-opentelemetry"
                _EXPORTER = "azure-monitor"
            else:
                from opentelemetry import trace
                from opentelemetry.sdk.resources import Resource
                from opentelemetry.sdk.trace import TracerProvider

                current_provider = trace.get_tracer_provider()
                if current_provider.__class__.__name__ == "ProxyTracerProvider":
                    trace.set_tracer_provider(
                        TracerProvider(
                            resource=Resource.create(
                                {
                                    "service.name": getattr(
                                        settings,
                                        "otel_service_name",
                                        "enterprise-ai-agent-supervisor",
                                    )
                                }
                            )
                        )
                    )

                _PROVIDER = "opentelemetry-sdk"
                _EXPORTER = "none"

        except Exception as exc:
            _INITIALIZATION_ERROR = f"{type(exc).__name__}: {exc}"
            _PROVIDER = "fallback"
            _EXPORTER = "none"
            LOGGER.warning(
                "OpenTelemetry initialization failed; continuing without "
                "external telemetry export: %s",
                _INITIALIZATION_ERROR,
            )

        return telemetry_status()


def telemetry_status() -> dict[str, Any]:
    return {
        "enabled": bool(getattr(settings, "otel_enabled", False)),
        "initialized": _INITIALIZED,
        "provider": _PROVIDER,
        "exporter": _EXPORTER,
        "service_name": getattr(
            settings,
            "otel_service_name",
            "enterprise-ai-agent-supervisor",
        ),
        "application_insights_configured": bool(
            getattr(
                settings,
                "applicationinsights_connection_string",
                None,
            )
        ),
        "error": _INITIALIZATION_ERROR,
    }


def get_tracer():
    initialize_telemetry()

    try:
        from opentelemetry import trace

        return trace.get_tracer(
            getattr(
                settings,
                "otel_service_name",
                "enterprise-ai-agent-supervisor",
            )
        )
    except Exception:
        return None


@contextmanager
def telemetry_span(
    name: str,
    *,
    attributes: dict[str, Any] | None = None,
) -> Iterator[Any]:
    """Create a real OpenTelemetry span when telemetry is enabled.

    When telemetry is disabled or unavailable, this behaves as a transparent
    no-op and yields ``None``. Exceptions raised by the business code are never
    swallowed.
    """

    if not getattr(settings, "otel_enabled", False):
        yield None
        return

    tracer = get_tracer()
    if tracer is None:
        yield None
        return

    context = get_request_context()
    span_attributes = {
        key: _safe_attribute_value(value)
        for key, value in (attributes or {}).items()
        if value is not None
    }

    if context is not None:
        span_attributes.setdefault("app.trace_id", context.trace_id)
        span_attributes.setdefault("app.request_id", context.request_id)
        if context.session_id:
            span_attributes.setdefault("app.session_id", context.session_id)
        if context.endpoint:
            span_attributes.setdefault("http.route", context.endpoint)

    try:
        span_manager = tracer.start_as_current_span(
            name,
            attributes=span_attributes,
        )
        span = span_manager.__enter__()
    except Exception:
        # Failure to start instrumentation must not break the request.
        yield None
        return

    try:
        yield span
    except BaseException as exc:
        # Let the OpenTelemetry context manager observe the same exception,
        # then propagate it unchanged to the application.
        span_manager.__exit__(
            type(exc),
            exc,
            exc.__traceback__,
        )
        raise
    else:
        span_manager.__exit__(None, None, None)


def add_telemetry_event(name: str, **attributes: Any) -> None:
    try:
        from opentelemetry import trace

        span = trace.get_current_span()
        if span is None or not span.is_recording():
            return

        span.add_event(
            name,
            attributes={
                key: _safe_attribute_value(value)
                for key, value in attributes.items()
                if value is not None
            },
        )
    except Exception:
        return


def record_telemetry_exception(exc: Exception) -> None:
    try:
        from opentelemetry import trace
        from opentelemetry.trace import Status, StatusCode

        span = trace.get_current_span()
        if span is None or not span.is_recording():
            return

        span.record_exception(exc)
        span.set_status(Status(StatusCode.ERROR, str(exc)))
    except Exception:
        return


def instrument_fastapi_app(app: Any) -> bool:
    """Instrument a FastAPI app explicitly when automatic instrumentation is absent."""

    initialize_telemetry()

    if not getattr(settings, "otel_enabled", False):
        return False

    try:
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

        FastAPIInstrumentor.instrument_app(app)
        return True
    except Exception as exc:
        LOGGER.warning("FastAPI instrumentation was not enabled: %s", exc)
        return False


def instrument_http_clients() -> dict[str, bool]:
    """Enable dependency spans for requests and httpx clients."""

    initialize_telemetry()
    results = {"requests": False, "httpx": False}

    if not getattr(settings, "otel_enabled", False):
        return results

    try:
        from opentelemetry.instrumentation.requests import RequestsInstrumentor

        RequestsInstrumentor().instrument()
        results["requests"] = True
    except Exception:
        pass

    try:
        from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor

        HTTPXClientInstrumentor().instrument()
        results["httpx"] = True
    except Exception:
        pass

    return results


def reset_telemetry_state_for_tests() -> None:
    global _INITIALIZED, _INITIALIZATION_ERROR, _PROVIDER, _EXPORTER

    with _INITIALIZATION_LOCK:
        _INITIALIZED = False
        _INITIALIZATION_ERROR = None
        _PROVIDER = "disabled"
        _EXPORTER = "none"
