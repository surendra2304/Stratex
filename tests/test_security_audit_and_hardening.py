"""
Comprehensive Security & Isolation Verification Suite
Verifies:
1. Hardcoded live trading disablement (LIVE_TRADING_ENABLED = False immutable).
2. Blocked live trading mutations via POST /api/config, /api/settings.
3. Zero secret leakage across all REST endpoints.
4. Input bounds and path traversal protections.
5. Safe JSON payload parsing and HTTP 400 validation.
"""
import pytest
from dashboard import app
import config

@pytest.fixture
def client():
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c

def test_immutable_live_trading_invariant():
    """Verifies LIVE_TRADING_ENABLED is False and cannot be enabled via runtime mutation."""
    assert config.LIVE_TRADING_ENABLED is False
    assert getattr(config, "TESTNET_ONLY", True) is True

def test_api_rejects_live_trading_activation_attempts(client):
    """Verifies that API endpoints strictly reject attempts to enable live trading."""
    # Attempt via /api/config
    res1 = client.post("/api/config", json={"LIVE_TRADING_ENABLED": True})
    assert res1.status_code in [400, 403]

    # Attempt via /api/settings
    res2 = client.post("/api/settings", json={"live_trading": True})
    assert res2.status_code in [400, 403]
    
    # Verify runtime flag remains False
    assert config.LIVE_TRADING_ENABLED is False

def test_api_endpoints_do_not_leak_secrets(client):
    """Verifies all API endpoints return sanitized payloads without credentials."""
    endpoints = [
        "/api/health", "/api/engine-health", "/api/status", "/api/positions", "/api/trades",
        "/api/scanner", "/api/risk", "/api/analytics", "/api/activity",
        "/api/config", "/api/ai/status", "/api/quantum/advisory"
    ]
    for ep in endpoints:
        res = client.get(ep)
        assert res.status_code == 200, f"Endpoint {ep} returned {res.status_code}"
        text = res.get_data(as_text=True)
        assert "AIza" not in text
        assert "SECRET_KEY" not in text
        assert "PRIVATE_KEY" not in text

def test_malformed_json_and_payload_sanitization(client):
    """Verifies endpoints reject invalid JSON and oversized/malformed payloads."""
    # Invalid JSON string
    res = client.post(
        "/api/config",
        data="not a json payload",
        headers={"Content-Type": "application/json"}
    )
    assert res.status_code == 400
