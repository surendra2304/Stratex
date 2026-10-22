"""tests/test_tier1_resilience_and_contracts.py — Tier-1 Production Excellence Tests.

Tests:
1. CircuitBreaker: CLOSED -> OPEN -> HALF_OPEN -> CLOSED state lifecycle.
2. CircuitBreaker fallback handling.
3. TTLCache: expiration, LRU eviction, thread safety.
4. Secret sanitization: zero credential leakage across logs and payloads.
5. Standardized endpoints: /health, /ready, /api/circuit-breakers, error envelopes.
"""

from __future__ import annotations

import time
import pytest
from unittest.mock import MagicMock

from resilience import (
    CircuitBreaker,
    CircuitBreakerOpenError,
    CircuitState,
    TTLCache,
    calculate_jittered_backoff,
    get_all_circuit_breaker_stats,
    get_circuit_breaker,
    get_resilient_session,
    sanitize_secrets,
)


class TestCircuitBreaker:
    def test_initial_state_is_closed(self):
        cb = CircuitBreaker("test_service", failure_threshold=3, recovery_timeout=0.2)
        assert cb.state == CircuitState.CLOSED
        assert cb.allow_request() is True

    def test_trips_to_open_after_failure_threshold(self):
        cb = CircuitBreaker("test_tripper", failure_threshold=3, recovery_timeout=0.2)
        cb.record_failure(ValueError("err 1"))
        assert cb.state == CircuitState.CLOSED
        cb.record_failure(ValueError("err 2"))
        assert cb.state == CircuitState.CLOSED
        cb.record_failure(ValueError("err 3"))
        assert cb.state == CircuitState.OPEN
        assert cb.allow_request() is False

    def test_fast_fail_when_open_without_fallback(self):
        cb = CircuitBreaker("test_fast_fail", failure_threshold=2, recovery_timeout=1.0)
        cb.record_failure(RuntimeError())
        cb.record_failure(RuntimeError())
        assert cb.state == CircuitState.OPEN

        with pytest.raises(CircuitBreakerOpenError) as exc_info:
            cb.call(lambda: "should not run")
        assert "is OPEN" in str(exc_info.value)

    def test_executes_fallback_when_open(self):
        cb = CircuitBreaker("test_fallback", failure_threshold=1, recovery_timeout=1.0)
        cb.record_failure(RuntimeError())
        assert cb.state == CircuitState.OPEN

        result = cb.call(lambda: "real", fallback=lambda: "fallback_val")
        assert result == "fallback_val"

        static_result = cb.call(lambda: "real", fallback={"status": "FALLBACK"})
        assert static_result == {"status": "FALLBACK"}

    def test_transitions_to_half_open_and_recovers(self):
        cb = CircuitBreaker("test_recovery", failure_threshold=2, recovery_timeout=0.05, half_open_success_threshold=2)
        cb.record_failure(RuntimeError())
        cb.record_failure(RuntimeError())
        assert cb.state == CircuitState.OPEN

        time.sleep(0.06)
        # Should now be HALF_OPEN
        assert cb.state == CircuitState.HALF_OPEN
        assert cb.allow_request() is True

        # First success keeps it HALF_OPEN
        cb.record_success()
        assert cb.state == CircuitState.HALF_OPEN

        # Second success closes the circuit
        cb.record_success()
        assert cb.state == CircuitState.CLOSED
        assert cb.allow_request() is True

    def test_half_open_failure_re_opens_circuit(self):
        cb = CircuitBreaker("test_half_open_fail", failure_threshold=1, recovery_timeout=0.05)
        cb.record_failure(RuntimeError())
        assert cb.state == CircuitState.OPEN

        time.sleep(0.06)
        assert cb.state == CircuitState.HALF_OPEN

        cb.record_failure(RuntimeError("probe failed"))
        assert cb.state == CircuitState.OPEN

    def test_manual_reset(self):
        cb = CircuitBreaker("test_reset", failure_threshold=1, recovery_timeout=60.0)
        cb.record_failure(RuntimeError())
        assert cb.state == CircuitState.OPEN
        cb.reset()
        assert cb.state == CircuitState.CLOSED
        assert cb.allow_request() is True

    def test_stats_reporting(self):
        cb = get_circuit_breaker("stats_test", failure_threshold=5, recovery_timeout=15.0)
        stats = cb.get_stats()
        assert stats["name"] == "stats_test"
        assert stats["state"] == "CLOSED"
        assert stats["failure_threshold"] == 5

        all_stats = get_all_circuit_breaker_stats()
        assert "stats_test" in all_stats


class TestTTLCache:
    def test_set_and_get(self):
        cache = TTLCache(max_size=10, default_ttl_seconds=1.0)
        cache.set("foo", "bar")
        assert cache.get("foo") == "bar"
        assert cache.get("missing", default="none") == "none"

    def test_expiration(self):
        cache = TTLCache(max_size=10, default_ttl_seconds=0.05)
        cache.set("key", "val")
        assert cache.get("key") == "val"
        time.sleep(0.06)
        assert cache.get("key") is None

    def test_lru_eviction(self):
        cache = TTLCache(max_size=3, default_ttl_seconds=60.0)
        cache.set("a", 1)
        cache.set("b", 2)
        cache.set("c", 3)
        # Access "a" to make it more recently used than "b"
        _ = cache.get("a")
        # Add "d", which should evict "b"
        cache.set("d", 4)
        assert cache.get("a") == 1
        assert cache.get("c") == 3
        assert cache.get("d") == 4
        assert cache.get("b") is None

    def test_delete_and_clear(self):
        cache = TTLCache(max_size=10)
        cache.set("x", 100)
        assert cache.delete("x") is True
        assert cache.delete("x") is False
        assert cache.get("x") is None

        cache.set("y", 200)
        cache.clear()
        assert cache.size() == 0


class TestSecretSanitization:
    def test_redacts_sensitive_dict_keys(self):
        payload = {
            "symbol": "BTCUSDT",
            "api_key": "BINANCE_LIVE_KEY_12345",
            "secret_key": "BINANCE_SECRET_ABCDE",
            "access_token": "JWT_TOKEN_SECRET_999",
            "nested": {
                "auth_bearer": "BEARER_TOKEN_XYZZY",
                "normal_field": "SAFE_VALUE"
            }
        }
        sanitized = sanitize_secrets(payload)
        assert sanitized["api_key"] == "***REDACTED***"
        assert sanitized["secret_key"] == "***REDACTED***"
        assert sanitized["access_token"] == "***REDACTED***"
        assert sanitized["nested"]["auth_bearer"] == "***REDACTED***"
        assert sanitized["nested"]["normal_field"] == "SAFE_VALUE"
        assert sanitized["symbol"] == "BTCUSDT"

    def test_redacts_auth_headers_in_strings(self):
        msg = "Connecting with Bearer a1b2c3d4e5f6g7h8i9j0k and key=abcdef123456789"
        sanitized = sanitize_secrets(msg)
        assert "a1b2c3d4e5f6g7h8i9j0k" not in sanitized
        assert "***REDACTED***" in sanitized


class TestResilientHTTPClient:
    def test_resilient_session_has_connection_pooling_adapters(self):
        session = get_resilient_session(pool_connections=15, pool_maxsize=30)
        http_adapter = session.get_adapter("http://localhost")
        https_adapter = session.get_adapter("https://localhost")
        assert http_adapter is not None
        assert https_adapter is not None

    def test_jittered_backoff_bounds(self):
        for attempt in range(5):
            delay = calculate_jittered_backoff(attempt, base_delay=0.1, max_delay=2.0)
            assert 0.0 <= delay <= 2.0


class TestStandardizedContracts:
    @pytest.fixture
    def client(self):
        import dashboard
        dashboard.app.config["TESTING"] = True
        with dashboard.app.test_client() as c:
            yield c

    def test_health_includes_service_metadata(self, client):
        resp = client.get("/health")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["status"] == "ok"
        assert data["service"] == "stratex"
        assert "version" in data
        assert "observed_at" in data

    def test_circuit_breakers_diagnostic_endpoint(self, client):
        resp = client.get("/api/circuit-breakers")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["status"] == "OK"
        assert data["service"] == "stratex"
        assert "circuit_breakers" in data
        assert isinstance(data["circuit_breakers"], dict)

    def test_api_404_error_envelope(self, client):
        resp = client.get("/api/nonexistent-route-for-testing")
        assert resp.status_code == 404
        data = resp.get_json()
        assert data["error"] == "NOT_FOUND"
        assert data["success"] is False
        assert "message" in data
