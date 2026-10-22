from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from enum import Enum


class CircuitState(str, Enum):
    CLOSED = "CLOSED"
    OPEN = "OPEN"
    HALF_OPEN = "HALF_OPEN"


@dataclass(slots=True)
class CircuitConfig:
    failure_threshold: int = 3
    recovery_seconds: float = 30.0


class CircuitBreaker:
    def __init__(self, config: CircuitConfig | None = None):
        self.config = config or CircuitConfig()
        self.state = CircuitState.CLOSED
        self.failures = 0
        self.opened_at = 0.0
        self._lock = threading.RLock()

    def allow(self) -> bool:
        with self._lock:
            if self.state == CircuitState.CLOSED:
                return True
            if self.state == CircuitState.OPEN and time.monotonic() - self.opened_at >= self.config.recovery_seconds:
                self.state = CircuitState.HALF_OPEN
                return True
            return self.state == CircuitState.HALF_OPEN

    def record_success(self) -> None:
        with self._lock:
            self.failures = 0
            self.state = CircuitState.CLOSED

    def record_failure(self) -> None:
        with self._lock:
            self.failures += 1
            if self.failures >= self.config.failure_threshold:
                self.state = CircuitState.OPEN
                self.opened_at = time.monotonic()
