from __future__ import annotations

import time
from dataclasses import dataclass
from enum import Enum
from threading import RLock
from typing import Callable


class CircuitState(str, Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class CircuitBreakerOpenError(RuntimeError):
    """Raised when a circuit breaker is open and blocks a service call."""

    def __init__(self, circuit_name: str, retry_after_seconds: float) -> None:
        self.circuit_name = circuit_name
        self.retry_after_seconds = max(0.0, retry_after_seconds)
        super().__init__(
            f"Circuit breaker '{circuit_name}' is open. "
            f"Retry after {self.retry_after_seconds:.2f} seconds."
        )


@dataclass(frozen=True)
class CircuitBreakerSnapshot:
    name: str
    state: str
    failure_count: int
    failure_threshold: int
    recovery_timeout_seconds: float
    retry_after_seconds: float
    half_open_probe_in_flight: bool


class CircuitBreaker:
    """Thread-safe circuit breaker with CLOSED, OPEN and HALF_OPEN states.

    A single probe is allowed in HALF_OPEN. A successful probe closes the
    circuit; a failed probe opens it again for another recovery interval.
    """

    def __init__(
        self,
        *,
        name: str,
        failure_threshold: int = 3,
        recovery_timeout_seconds: float = 30.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if failure_threshold < 1:
            raise ValueError("failure_threshold must be at least 1.")
        if recovery_timeout_seconds <= 0:
            raise ValueError("recovery_timeout_seconds must be greater than 0.")

        self.name = name
        self.failure_threshold = failure_threshold
        self.recovery_timeout_seconds = recovery_timeout_seconds
        self._clock = clock
        self._lock = RLock()

        self._state = CircuitState.CLOSED
        self._failure_count = 0
        self._opened_at: float | None = None
        self._half_open_probe_in_flight = False

    def before_call(self) -> None:
        """Allow a call or raise CircuitBreakerOpenError."""

        with self._lock:
            now = self._clock()

            if self._state == CircuitState.CLOSED:
                return

            if self._state == CircuitState.OPEN:
                retry_after = self._retry_after_locked(now)
                if retry_after > 0:
                    raise CircuitBreakerOpenError(self.name, retry_after)

                self._state = CircuitState.HALF_OPEN
                self._half_open_probe_in_flight = False

            if self._state == CircuitState.HALF_OPEN:
                if self._half_open_probe_in_flight:
                    raise CircuitBreakerOpenError(
                        self.name,
                        self.recovery_timeout_seconds,
                    )
                self._half_open_probe_in_flight = True

    def record_success(self) -> None:
        """Close the circuit and reset failure counters."""

        with self._lock:
            self._state = CircuitState.CLOSED
            self._failure_count = 0
            self._opened_at = None
            self._half_open_probe_in_flight = False

    def record_failure(self) -> None:
        """Record a service failure and open the circuit when required."""

        with self._lock:
            now = self._clock()

            if self._state == CircuitState.HALF_OPEN:
                self._open_locked(now)
                return

            if self._state == CircuitState.OPEN:
                self._opened_at = now
                self._half_open_probe_in_flight = False
                return

            self._failure_count += 1
            if self._failure_count >= self.failure_threshold:
                self._open_locked(now)

    def snapshot(self) -> CircuitBreakerSnapshot:
        with self._lock:
            now = self._clock()
            return CircuitBreakerSnapshot(
                name=self.name,
                state=self._state.value,
                failure_count=self._failure_count,
                failure_threshold=self.failure_threshold,
                recovery_timeout_seconds=self.recovery_timeout_seconds,
                retry_after_seconds=self._retry_after_locked(now),
                half_open_probe_in_flight=self._half_open_probe_in_flight,
            )

    def reset(self) -> None:
        self.record_success()

    def _open_locked(self, now: float) -> None:
        self._state = CircuitState.OPEN
        self._opened_at = now
        self._half_open_probe_in_flight = False

    def _retry_after_locked(self, now: float) -> float:
        if self._state != CircuitState.OPEN or self._opened_at is None:
            return 0.0

        elapsed = max(0.0, now - self._opened_at)
        return max(0.0, self.recovery_timeout_seconds - elapsed)
