from __future__ import annotations

from dataclasses import replace

from shared import telemetry


def _replace_settings(monkeypatch, **changes):
    updated = replace(telemetry.settings, **changes)
    monkeypatch.setattr(telemetry, "settings", updated)
    return updated


def test_telemetry_is_disabled_without_configuration(monkeypatch):
    _replace_settings(
        monkeypatch,
        otel_enabled=False,
        applicationinsights_connection_string=None,
    )
    telemetry.reset_telemetry_state_for_tests()

    status = telemetry.initialize_telemetry()

    assert status["enabled"] is False
    assert status["initialized"] is True
    assert status["provider"] == "disabled"
    assert status["exporter"] == "none"


def test_telemetry_initialization_fails_open(monkeypatch):
    _replace_settings(
        monkeypatch,
        otel_enabled=True,
        applicationinsights_connection_string="InstrumentationKey=test",
    )
    telemetry.reset_telemetry_state_for_tests()

    real_import = __import__

    def failing_import(name, *args, **kwargs):
        if name.startswith("azure.monitor.opentelemetry"):
            raise ImportError("package unavailable")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr("builtins.__import__", failing_import)

    status = telemetry.initialize_telemetry()

    assert status["initialized"] is True
    assert status["provider"] == "fallback"
    assert status["exporter"] == "none"
    assert status["error"] is not None
    assert "ImportError" in status["error"]


def test_telemetry_span_is_safe_when_disabled(monkeypatch):
    _replace_settings(
        monkeypatch,
        otel_enabled=False,
        applicationinsights_connection_string=None,
    )
    telemetry.reset_telemetry_state_for_tests()

    with telemetry.telemetry_span(
        "test.operation",
        attributes={"agent": "supervisor"},
    ) as span:
        assert span is None
