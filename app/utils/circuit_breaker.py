"""
Circuit breaker for external service calls.

Generic decorator-style circuit breaker with three states:
  CLOSED    → normal operation
  OPEN      → block requests after ``failure_threshold`` failures
  HALF_OPEN → probe recovery after ``timeout_duration`` seconds

Usage::

    from app.utils.circuit_breaker import CircuitBreaker

    my_breaker = CircuitBreaker(failure_threshold=5, timeout_duration=30, name="my_api")

    @my_breaker
    async def call_external() -> ...:
        ...

Raises ``CircuitBreakerOpenError`` when the breaker is open.
"""

import time
from collections.abc import Callable
from functools import wraps
from typing import Any

from app.utils.logging import log


class CircuitBreakerOpenError(Exception):
    """Raised when the circuit breaker is in OPEN state."""


class CircuitBreaker:
    """Simple decorator-based circuit breaker (async)."""

    def __init__(
        self,
        failure_threshold: int = 5,
        timeout_duration: int = 30,
        name: str = "circuit_breaker",
    ) -> None:
        self.failure_threshold = failure_threshold
        self.timeout_duration = timeout_duration
        self.name = name
        self.failure_count: int = 0
        self.last_failure_time: float | None = None
        self.state: str = "CLOSED"  # CLOSED | OPEN | HALF_OPEN

    def __call__(self, func: Callable) -> Callable:
        @wraps(func)
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            if self.state == "OPEN":
                if self._should_attempt_reset():
                    self.state = "HALF_OPEN"
                    log.info(f"[circuit_breaker:{self.name}] → HALF_OPEN")
                else:
                    log.warning(f"[circuit_breaker:{self.name}] OPEN — blocking call")
                    raise CircuitBreakerOpenError(
                        f"Circuit breaker '{self.name}' is OPEN; service temporarily unavailable."
                    )
            try:
                result = await func(*args, **kwargs)
                if self.state == "HALF_OPEN":
                    self._reset()
                    log.info(f"[circuit_breaker:{self.name}] Recovered → CLOSED")
                return result
            except Exception:
                self._record_failure()
                raise

        return wrapper

    def _should_attempt_reset(self) -> bool:
        if self.last_failure_time is None:
            return True
        return (time.time() - self.last_failure_time) >= self.timeout_duration

    def _record_failure(self) -> None:
        self.failure_count += 1
        self.last_failure_time = time.time()
        if self.failure_count >= self.failure_threshold:
            self.state = "OPEN"
            log.error(f"[circuit_breaker:{self.name}] Opened after {self.failure_count} failures")

    def _reset(self) -> None:
        self.failure_count = 0
        self.last_failure_time = None
        self.state = "CLOSED"
