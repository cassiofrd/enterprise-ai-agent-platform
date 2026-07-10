from __future__ import annotations

import pytest

from shared.circuit_breaker import (
    CircuitBreaker,
    CircuitBreakerOpenError,
)


class FakeClock:
    def __init__(self) -> None:
        self.value = 0.0

    def __call__(self) -> float:
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += seconds


def test_circuit_opens_after_failure_threshold():
    clock = FakeClock()
    breaker = CircuitBreaker(
        name="inventory",
        failure_threshold=2,
        recovery_timeout_seconds=10,
        clock=clock,
    )

    breaker.before_call()
    breaker.record_failure()
    assert breaker.snapshot().state == "closed"

    breaker.before_call()
    breaker.record_failure()
    snapshot = breaker.snapshot()
    assert snapshot.state == "open"
    assert snapshot.failure_count == 2

    with pytest.raises(CircuitBreakerOpenError):
        breaker.before_call()


def test_circuit_moves_to_half_open_and_closes_after_success():
    clock = FakeClock()
    breaker = CircuitBreaker(
        name="supplier",
        failure_threshold=1,
        recovery_timeout_seconds=5,
        clock=clock,
    )

    breaker.before_call()
    breaker.record_failure()
    assert breaker.snapshot().state == "open"

    clock.advance(5)
    breaker.before_call()
    assert breaker.snapshot().state == "half_open"

    breaker.record_success()
    snapshot = breaker.snapshot()
    assert snapshot.state == "closed"
    assert snapshot.failure_count == 0


def test_half_open_allows_only_one_probe():
    clock = FakeClock()
    breaker = CircuitBreaker(
        name="inventory",
        failure_threshold=1,
        recovery_timeout_seconds=3,
        clock=clock,
    )

    breaker.before_call()
    breaker.record_failure()
    clock.advance(3)

    breaker.before_call()

    with pytest.raises(CircuitBreakerOpenError):
        breaker.before_call()


def test_failed_half_open_probe_reopens_circuit():
    clock = FakeClock()
    breaker = CircuitBreaker(
        name="supplier",
        failure_threshold=1,
        recovery_timeout_seconds=4,
        clock=clock,
    )

    breaker.before_call()
    breaker.record_failure()
    clock.advance(4)

    breaker.before_call()
    breaker.record_failure()

    snapshot = breaker.snapshot()
    assert snapshot.state == "open"
    assert snapshot.retry_after_seconds == pytest.approx(4)
