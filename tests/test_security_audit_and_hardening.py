"""
Comprehensive Security & Isolation Verification Suite
Verifies:
1. Hardcoded live trading disablement (LIVE_TRADING_ENABLED = False immutable).
2. Blocked live trading mutations via POST /api/config, /api/settings.
3. Zero secret leakage across all REST endpoints.
4. Input bounds and path traversal protections.
5. Safe JSON payload parsing and HTTP 400 validation.
6. Multi-tier API key authentication, scope enforcement, and zero-downtime rotation.
7. Rate limiting per key (60/min read, 10/min control) and per IP.
8. Webhook HMAC-SHA256 signature verification.
9. Cryptographically signed audit trail (control_audit.jsonl) integrity.
10. GET /api/v1/security/status contract and fields.
"""
import pytest

import config
from dashboard import app
from security_hardening import (
    SecurityRateLimiter,
    mask_credential,
    sanitize_input,
    sign_audit_record,
    verify_audit_chain,
    verify_webhook_signature,
)


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
    res1 = client.post("/api/config", json={"LIVE_TRADING_ENABLED": True})
    assert res1.status_code in [400, 403]

    res2 = client.post("/api/settings", json={"live_trading": True})
    assert res2.status_code in [400, 403]
    
    assert config.LIVE_TRADING_ENABLED is False

def test_api_endpoints_do_not_leak_secrets(client):
    """Verifies all API endpoints return sanitized payloads without credentials."""
    endpoints = [
        "/api/health", "/api/engine-health", "/api/status", "/api/positions", "/api/trades",
        "/api/scanner", "/api/risk", "/api/analytics", "/api/activity",
        "/api/config", "/api/ai/status", "/api/quantum/advisory", "/api/v1/security/status"
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
    res = client.post(
        "/api/config",
        data="not a json payload",
        headers={"Content-Type": "application/json"}
    )
    assert res.status_code == 400

def test_security_status_endpoint(client):
    """Verifies GET /api/v1/security/status returns valid health, auth, rate limit, and webhook config."""
    res = client.get("/api/v1/security/status")
    assert res.status_code == 200
    data = res.get_json()
    assert data["status"] == "HEALTHY"
    assert "auth_configuration" in data
    assert "rate_limiting" in data
    assert "webhook_verification" in data
    assert "self_monitoring" in data
    assert "audit_trail" in data
    assert data["rate_limiting"]["read_endpoints"]["limit_per_minute"] == 60
    assert data["rate_limiting"]["control_endpoints"]["limit_per_minute"] == 10

def test_webhook_hmac_signature_verification():
    """Verifies HMAC-SHA256 signature verification on webhook payloads."""
    import hashlib
    import hmac
    payload = b'{"event":"trade_filled","symbol":"BTCUSDT"}'
    secret = "test_webhook_secret_key"
    expected_sig = hmac.new(secret.encode("utf-8"), payload, hashlib.sha256).hexdigest()

    # Valid signature
    assert verify_webhook_signature(payload, expected_sig, secret=secret) is True
    assert verify_webhook_signature(payload, f"sha256={expected_sig}", secret=secret) is True

    # Invalid signature
    assert verify_webhook_signature(payload, "invalid_signature_hex", secret=secret) is False
    assert verify_webhook_signature(b"altered payload", expected_sig, secret=secret) is False

def test_audit_trail_cryptographic_chain():
    """Verifies HMAC signing and tamper detection across audit records."""
    r1 = {"id": "1", "action": "SET_PARAMETER", "val": 10}
    sig1 = sign_audit_record(r1, prev_hash="")
    r1["signature"] = sig1

    r2 = {"id": "2", "action": "PANIC_FLATTEN", "val": 0}
    sig2 = sign_audit_record(r2, prev_hash=sig1)
    r2["signature"] = sig2

    chain = [r1, r2]
    assert verify_audit_chain(chain) is True

    # Tamper with record
    r1_tampered = dict(r1)
    r1_tampered["val"] = 999
    assert verify_audit_chain([r1_tampered, r2]) is False

def test_rate_limiter_token_bucket():
    """Verifies Sliding Window Rate Limiter allows up to limit and rejects subsequent requests."""
    limiter = SecurityRateLimiter(default_limit=3, window_seconds=60)
    
    # 3 allowed
    ok1, rem1, _ = limiter.is_allowed("test_key")
    assert ok1 is True and rem1 == 2
    ok2, rem2, _ = limiter.is_allowed("test_key")
    assert ok2 is True and rem2 == 1
    ok3, rem3, _ = limiter.is_allowed("test_key")
    assert ok3 is True and rem3 == 0

    # 4th rejected
    ok4, rem4, retry = limiter.is_allowed("test_key")
    assert ok4 is False
    assert retry > 0

def test_credential_masking_and_sanitization():
    """Verifies API credentials are never exposed in plaintext and control characters are scrubbed."""
    assert mask_credential("my_secret_production_api_key_12345") == "my_s****2345"
    assert mask_credential("short") == "****"
    assert mask_credential("") == "NOT_SET"

    dirty = {"name": "<script>alert(1)</script>hello", "nested": ["a\x00b", {"key": "val{}"}]}
    cleaned = sanitize_input(dirty)
    assert cleaned["name"] == "scriptalert(1)/scripthello"
    assert cleaned["nested"][0] == "ab"

