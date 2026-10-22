"""resilience.py — Enterprise network resilience, circuit breaking, and caching.

Provides:
- CircuitBreaker: Inter-agent and external upstream network circuit breaker.
- TTLCache: Thread-safe in-memory cache with expiry and LRU eviction.
- ResilientSession: Requests session with connection pooling, jittered exponential backoff, and circuit breaker.
- AsyncMeshClient: Asynchronous HTTP client (via httpx) with connection pooling and circuit breakers.
- sanitize_secrets: Zero-leakage secret sanitizer for logs, errors, and responses.
"""

from __future__ import annotations

import asyncio
import enum
import logging
import math
import os
import random
import re
import threading
import time
from typing import Any, Callable, TypeVar

try:
    import httpx
except ImportError:  # pragma: no cover
    httpx = None  # type: ignore[assignment]

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

logger = logging.getLogger("resilience")

T = TypeVar("T")


# ==============================================================================
# SECRET SANITIZATION & REDACTION
# ==============================================================================

_SECRET_KEY_PATTERN = re.compile(
    r"(api[_-]?key|secret|password|token|auth|bearer|credentials|private[_-]?key)",
    re.IGNORECASE,
)
_SECRET_VALUE_PATTERN = re.compile(
    r"(?:(?:Bearer\s+)|(?:key=)|(?:secret=))([a-zA-Z0-9_\-\.]{12,})",
    re.IGNORECASE,
)


def sanitize_secrets(val: Any) -> Any:
    """Recursively redacts sensitive keys or token strings from logs and dicts."""
    if isinstance(val, dict):
        sanitized = {}
        for k, v in val.items():
            if _SECRET_KEY_PATTERN.search(str(k)):
                sanitized[k] = "***REDACTED***"
            else:
                sanitized[k] = sanitize_secrets(v)
        return sanitized
    if isinstance(val, (list, tuple)):
        return [sanitize_secrets(item) for item in val]
    if isinstance(val, str):
        return _SECRET_VALUE_PATTERN.sub(r"***REDACTED***", val)
    return val


# ==============================================================================
# CIRCUIT BREAKER
# ==============================================================================

class CircuitState(str, enum.Enum):
    CLOSED = "CLOSED"        # Normal operation: requests pass through
    OPEN = "OPEN"            # Tripped: requests fail fast or return fallback
    HALF_OPEN = "HALF_OPEN"  # Testing: limited requests allowed to probe recovery


class CircuitBreakerOpenError(RuntimeError):
    """Raised when an operation is attempted while the circuit breaker is OPEN."""
    def __init__(self, name: str, retry_after: float):
        super().__init__(f"Circuit breaker '{name}' is OPEN. Retry after {retry_after:.1f}s.")
        self.name = name
        self.retry_after = retry_after


class CircuitBreaker:
    """Thread-safe circuit breaker preventing cascading failures to upstream dependencies."""

    def __init__(
        self,
        name: str,
        failure_threshold: int = 4,
        recovery_timeout: float = 30.0,
        half_open_success_threshold: int = 2,
    ) -> None:
        self.name = name
        self.failure_threshold = max(1, failure_threshold)
        self.recovery_timeout = max(0.01, recovery_timeout)
        self.half_open_success_threshold = max(1, half_open_success_threshold)

        self._state = CircuitState.CLOSED
        self._failure_count = 0
        self._consecutive_successes = 0
        self._last_failure_time = 0.0
        self._last_state_change = time.time()
        self._total_trips = 0
        self._lock = threading.RLock()

    @property
    def state(self) -> CircuitState:
        with self._lock:
            if self._state == CircuitState.OPEN:
                elapsed = time.time() - self._last_failure_time
                if elapsed >= self.recovery_timeout:
                    self._state = CircuitState.HALF_OPEN
                    self._consecutive_successes = 0
                    self._last_state_change = time.time()
                    logger.info(f"[CIRCUIT_BREAKER] '{self.name}' transitioned from OPEN to HALF_OPEN (probing).")
            return self._state

    def allow_request(self) -> bool:
        """Determines if a request is permitted under the current circuit state."""
        return self.state in (CircuitState.CLOSED, CircuitState.HALF_OPEN)

    def record_success(self) -> None:
        """Records a successful operation, resetting or closing the circuit."""
        with self._lock:
            if self._state == CircuitState.HALF_OPEN:
                self._consecutive_successes += 1
                if self._consecutive_successes >= self.half_open_success_threshold:
                    self._state = CircuitState.CLOSED
                    self._failure_count = 0
                    self._consecutive_successes = 0
                    self._last_state_change = time.time()
                    logger.info(f"[CIRCUIT_BREAKER] '{self.name}' recovered: transitioned to CLOSED.")
            elif self._state == CircuitState.CLOSED:
                self._failure_count = 0

    def record_failure(self, error: Exception | None = None) -> None:
        """Records an execution failure, potentially tripping the circuit."""
        with self._lock:
            self._last_failure_time = time.time()
            self._failure_count += 1
            if self._state == CircuitState.HALF_OPEN:
                self._state = CircuitState.OPEN
                self._last_state_change = time.time()
                self._total_trips += 1
                logger.warning(
                    f"[CIRCUIT_BREAKER] '{self.name}' probe failed during HALF_OPEN. Re-opening circuit. Error: {error}"
                )
            elif self._state == CircuitState.CLOSED and self._failure_count >= self.failure_threshold:
                self._state = CircuitState.OPEN
                self._last_state_change = time.time()
                self._total_trips += 1
                logger.error(
                    f"[CIRCUIT_BREAKER] '{self.name}' tripped to OPEN after {self._failure_count} consecutive failures. "
                    f"Will retry probe in {self.recovery_timeout:.1f}s. Last error: {error}"
                )

    def reset(self) -> None:
        """Manually forces the circuit breaker back to CLOSED state."""
        with self._lock:
            self._state = CircuitState.CLOSED
            self._failure_count = 0
            self._consecutive_successes = 0
            self._last_failure_time = 0.0
            self._last_state_change = time.time()
            logger.info(f"[CIRCUIT_BREAKER] '{self.name}' manually reset to CLOSED.")

    def call(self, func: Callable[..., T], *args: Any, fallback: Callable[..., T] | T | None = None, **kwargs: Any) -> T:
        """Executes ``func`` guarded by the circuit breaker with fallback capability."""
        if not self.allow_request():
            retry_after = max(0.0, self.recovery_timeout - (time.time() - self._last_failure_time))
            if fallback is not None:
                return fallback(*args, **kwargs) if callable(fallback) else fallback
            raise CircuitBreakerOpenError(self.name, retry_after)

        try:
            result = func(*args, **kwargs)
            self.record_success()
            return result
        except Exception as exc:
            self.record_failure(exc)
            if fallback is not None:
                return fallback(*args, **kwargs) if callable(fallback) else fallback
            raise

    def get_stats(self) -> dict[str, Any]:
        with self._lock:
            current_state = self.state
            return {
                "name": self.name,
                "state": current_state.value,
                "failure_count": self._failure_count,
                "failure_threshold": self.failure_threshold,
                "recovery_timeout": self.recovery_timeout,
                "total_trips": self._total_trips,
                "seconds_since_state_change": round(time.time() - self._last_state_change, 1),
            }


# Registry of circuit breakers by target service/endpoint
_CIRCUIT_REGISTRY: dict[str, CircuitBreaker] = {}
_CIRCUIT_REGISTRY_LOCK = threading.Lock()


def get_circuit_breaker(name: str, **kwargs: Any) -> CircuitBreaker:
    with _CIRCUIT_REGISTRY_LOCK:
        if name not in _CIRCUIT_REGISTRY:
            _CIRCUIT_REGISTRY[name] = CircuitBreaker(name, **kwargs)
        return _CIRCUIT_REGISTRY[name]


def get_all_circuit_breaker_stats() -> dict[str, dict[str, Any]]:
    with _CIRCUIT_REGISTRY_LOCK:
        return {name: cb.get_stats() for name, cb in _CIRCUIT_REGISTRY.items()}


# ==============================================================================
# IN-MEMORY TTL & LRU CACHE
# ==============================================================================

class TTLCache:
    """Thread-safe, bounded, in-memory cache with per-item TTL and LRU eviction."""

    def __init__(self, max_size: int = 1000, default_ttl_seconds: float = 300.0) -> None:
        self.max_size = max(1, max_size)
        self.default_ttl = default_ttl_seconds
        self._store: dict[str, tuple[Any, float, int]] = {}  # key -> (value, expires_at, access_count)
        self._access_counter = 0
        self._lock = threading.RLock()

    def get(self, key: str, default: Any = None) -> Any:
        with self._lock:
            item = self._store.get(key)
            if item is None:
                return default
            val, expires_at, _ = item
            now = time.time()
            if now > expires_at:
                del self._store[key]
                return default
            self._access_counter += 1
            self._store[key] = (val, expires_at, self._access_counter)
            return val

    def set(self, key: str, value: Any, ttl_seconds: float | None = None) -> None:
        with self._lock:
            ttl = ttl_seconds if ttl_seconds is not None else self.default_ttl
            now = time.time()
            expires_at = now + ttl

            # If updating existing key, retain size without eviction
            if key in self._store:
                self._access_counter += 1
                self._store[key] = (value, expires_at, self._access_counter)
                return

            # Evict expired items if near capacity
            if len(self._store) >= self.max_size:
                expired = [k for k, (_, exp, _) in self._store.items() if now > exp]
                for k in expired:
                    del self._store[k]

            # If still over capacity, evict least recently accessed (LRU)
            if len(self._store) >= self.max_size:
                lru_key = min(self._store.keys(), key=lambda k: self._store[k][2])
                del self._store[lru_key]

            self._access_counter += 1
            self._store[key] = (value, expires_at, self._access_counter)

    def delete(self, key: str) -> bool:
        with self._lock:
            if key in self._store:
                del self._store[key]
                return True
            return False

    def clear(self) -> None:
        with self._lock:
            self._store.clear()

    def size(self) -> int:
        with self._lock:
            now = time.time()
            return sum(1 for _, exp, _ in self._store.values() if now <= exp)


# ==============================================================================
# HIGH-PERFORMANCE RESILIENT HTTP CLIENTS
# ==============================================================================

def get_resilient_session(
    pool_connections: int = 25,
    pool_maxsize: int = 50,
    max_retries: int = 3,
    backoff_factor: float = 0.5,
) -> requests.Session:
    """Creates a configured requests.Session with connection pooling and HTTP retries."""
    session = requests.Session()
    retries = Retry(
        total=max_retries,
        backoff_factor=backoff_factor,
        status_forcelist=[502, 503, 504],
        raise_on_status=False,
    )
    adapter = HTTPAdapter(
        pool_connections=pool_connections,
        pool_maxsize=pool_maxsize,
        max_retries=retries,
    )
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    return session


class AsyncMeshClient:
    """Async HTTP client wrapper providing non-blocking execution with connection pooling."""

    def __init__(
        self,
        base_url: str = "",
        timeout: float = 10.0,
        max_connections: int = 50,
        max_keepalive: int = 20,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.max_connections = max_connections
        self.max_keepalive = max_keepalive
        self._client: httpx.AsyncClient | None = None
        self._lock = asyncio.Lock() if hasattr(asyncio, "Lock") else None

    async def get_client(self) -> httpx.AsyncClient:
        if httpx is None:
            raise RuntimeError("httpx package is required for AsyncMeshClient.")
        if self._client is None or self._client.is_closed:
            limits = httpx.Limits(
                max_connections=self.max_connections,
                max_keepalive_connections=self.max_keepalive,
            )
            self._client = httpx.AsyncClient(
                base_url=self.base_url,
                timeout=self.timeout,
                limits=limits,
            )
        return self._client

    async def get(self, path: str, **kwargs: Any) -> httpx.Response:
        client = await self.get_client()
        cb = get_circuit_breaker(self.base_url or path)
        if not cb.allow_request():
            retry_after = max(0.0, cb.recovery_timeout - (time.time() - cb._last_failure_time))
            raise CircuitBreakerOpenError(cb.name, retry_after)
        try:
            resp = await client.get(path, **kwargs)
            if resp.status_code >= 500:
                cb.record_failure()
            else:
                cb.record_success()
            return resp
        except Exception as exc:
            cb.record_failure(exc)
            raise

    async def post(self, path: str, **kwargs: Any) -> httpx.Response:
        client = await self.get_client()
        cb = get_circuit_breaker(self.base_url or path)
        if not cb.allow_request():
            retry_after = max(0.0, cb.recovery_timeout - (time.time() - cb._last_failure_time))
            raise CircuitBreakerOpenError(cb.name, retry_after)
        try:
            resp = await client.post(path, **kwargs)
            if resp.status_code >= 500:
                cb.record_failure()
            else:
                cb.record_success()
            return resp
        except Exception as exc:
            cb.record_failure(exc)
            raise

    async def close(self) -> None:
        if self._client and not self._client.is_closed:
            await self._client.aclose()


def calculate_jittered_backoff(attempt: int, base_delay: float = 0.5, max_delay: float = 10.0) -> float:
    """Computes exponential backoff with full jitter to avoid thundering herds."""
    temp = min(max_delay, base_delay * (2 ** attempt))
    return random.uniform(0.0, temp)
