from __future__ import annotations

import os
import platform
import sys
from datetime import datetime, timezone
from typing import Any, Callable


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def runtime_metadata(*, service_name: str) -> dict[str, Any]:
    return {
        "service": service_name,
        "version": os.getenv("APP_VERSION", "2.3.3"),
        "build_sha": os.getenv("BUILD_SHA") or os.getenv("GITHUB_SHA"),
        "environment": os.getenv("APP_ENVIRONMENT", "local"),
        "python_version": platform.python_version(),
        "platform": platform.system().lower(),
        "timestamp": now_iso(),
    }


def liveness_payload(*, service_name: str) -> dict[str, Any]:
    """Process-level health. This endpoint must not call external services."""

    return {
        "status": "alive",
        **runtime_metadata(service_name=service_name),
    }


def _safe_check(
    name: str,
    check: Callable[[], dict[str, Any]],
    *,
    required: bool,
) -> dict[str, Any]:
    try:
        details = check()
        available = bool(
            details.get("available", True)
            and details.get("status", "ok") not in {"error", "unavailable"}
        )
        return {
            "name": name,
            "required": required,
            "available": available,
            "details": details,
        }
    except Exception as exc:
        return {
            "name": name,
            "required": required,
            "available": False,
            "details": {
                "error": f"{type(exc).__name__}: {exc}",
            },
        }


def readiness_payload(
    *,
    service_name: str,
    checks: list[tuple[str, Callable[[], dict[str, Any]], bool]],
) -> tuple[dict[str, Any], int]:
    results = [
        _safe_check(name, check, required=required)
        for name, check, required in checks
    ]

    unavailable_required = [
        item["name"]
        for item in results
        if item["required"] and not item["available"]
    ]

    ready = not unavailable_required
    payload = {
        "status": "ready" if ready else "not_ready",
        **runtime_metadata(service_name=service_name),
        "checks": results,
        "unavailable_required_components": unavailable_required,
    }
    return payload, 200 if ready else 503


def diagnostics_payload(
    *,
    service_name: str,
    checks: list[tuple[str, Callable[[], dict[str, Any]], bool]],
    capabilities: list[str],
) -> dict[str, Any]:
    readiness, _ = readiness_payload(
        service_name=service_name,
        checks=checks,
    )
    return {
        **runtime_metadata(service_name=service_name),
        "status": readiness["status"],
        "capabilities": capabilities,
        "checks": readiness["checks"],
    }


def cache_readiness(cache_health: Callable[[], dict[str, Any]]) -> dict[str, Any]:
    status = cache_health()
    backend = status.get("backend")
    # Memory fallback is an intentional supported backend.
    status["available"] = backend in {"memory", "redis"}
    return status


def memory_readiness(memory_health: Callable[[], dict[str, Any]]) -> dict[str, Any]:
    status = memory_health()
    status["available"] = bool(status.get("available", True))
    return status


def search_readiness(search_status: Callable[[], dict[str, Any]]) -> dict[str, Any]:
    status = search_status()
    enabled = bool(status.get("enabled"))
    # Azure AI Search is optional locally. When enabled, configuration must be complete.
    status["available"] = (
        True
        if not enabled
        else bool(
            status.get("endpoint_configured")
            and status.get("key_configured")
            and status.get("index_name")
        )
    )
    return status


def telemetry_readiness(
    telemetry_status: Callable[[], dict[str, Any]],
) -> dict[str, Any]:
    status = telemetry_status()
    enabled = bool(status.get("enabled"))
    status["available"] = not enabled or not status.get("error")
    return status
