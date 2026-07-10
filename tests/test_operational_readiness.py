from __future__ import annotations

from shared.operational import (
    liveness_payload,
    readiness_payload,
)


def test_liveness_does_not_require_dependencies(monkeypatch):
    monkeypatch.setenv("APP_VERSION", "test-version")
    payload = liveness_payload(service_name="supervisor")

    assert payload["status"] == "alive"
    assert payload["service"] == "supervisor"
    assert payload["version"] == "test-version"


def test_readiness_returns_200_when_required_checks_pass():
    payload, status_code = readiness_payload(
        service_name="inventory",
        checks=[
            ("required", lambda: {"available": True}, True),
            ("optional", lambda: {"available": False}, False),
        ],
    )

    assert status_code == 200
    assert payload["status"] == "ready"
    assert payload["unavailable_required_components"] == []


def test_readiness_returns_503_when_required_check_fails():
    payload, status_code = readiness_payload(
        service_name="supplier",
        checks=[
            ("database", lambda: {"available": False}, True),
        ],
    )

    assert status_code == 503
    assert payload["status"] == "not_ready"
    assert payload["unavailable_required_components"] == ["database"]


def test_readiness_contains_exception_without_raising():
    def failing_check():
        raise RuntimeError("dependency failed")

    payload, status_code = readiness_payload(
        service_name="supervisor",
        checks=[("dependency", failing_check, True)],
    )

    assert status_code == 503
    check = payload["checks"][0]
    assert check["available"] is False
    assert "RuntimeError" in check["details"]["error"]
