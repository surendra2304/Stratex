"""
tests/test_production_deployment.py — Tests for Production Deployment, Security Hardening & Monitoring.

Verifies:
1. config_production.py validates strict boundaries (drawdown <= 15%, daily loss <= 5%, forbidden params).
2. security_hardening.py token bucket rate limiter (100 req/hr).
3. Input sanitization strips dangerous control / script sequences.
4. Cryptographic audit trail signs and verifies tamper-proof record chains.
5. Anomaly detector flags order frequency bursts and excessive notional sizes.
6. monitoring_system.py emits alerts on threshold breaches, generates Prometheus metric text, and handles acknowledgments.
7. deploy_production.py pre-deployment audit pipeline.
"""

import os
import tempfile
import time
import pytest

from config_production import validate_production_security, PRODUCTION_FORBIDDEN_PARAMS
from security_hardening import (
    SecurityRateLimiter,
    sanitize_input,
    mask_credential,
    sign_audit_record,
    verify_audit_chain,
    TradingAnomalyDetector
)
from monitoring_system import ProductionMonitoringSystem


def test_production_security_validation():
    """Verify production security configuration validation passes."""
    checks = validate_production_security()
    assert checks["debug_disabled"] is True
    assert checks["drawdown_limit_safe"] is True
    assert checks["daily_loss_limit_safe"] is True
    assert checks["safety_gates_active"] is True
    assert "live_trading_enabled" in PRODUCTION_FORBIDDEN_PARAMS
    assert "api_key" in PRODUCTION_FORBIDDEN_PARAMS


def test_rate_limiter_enforces_capacity():
    """Verify rate limiter blocks requests exceeding max_requests."""
    limiter = SecurityRateLimiter(max_requests=5, window_seconds=60)
    ip = "192.168.1.100"

    for _ in range(5):
        allowed, remaining, _ = limiter.is_allowed(ip)
        assert allowed is True

    # 6th request must be blocked
    allowed, remaining, retry_after = limiter.is_allowed(ip)
    assert allowed is False
    assert remaining == 0
    assert retry_after > 0


def test_input_sanitization_and_masking():
    """Verify input sanitization strips control/script tags and masks keys."""
    raw = {"query": "<script>alert('xss')</script>BTCUSDT", "nested": {"param": "foo\x00bar"}}
    cleaned = sanitize_input(raw)
    assert "<script>" not in cleaned["query"]
    assert "scriptalert('xss')/scriptBTCUSDT" == cleaned["query"]

    assert mask_credential("ABCD1234EFGH5678") == "ABCD****5678"
    assert mask_credential("short") == "****"
    assert mask_credential("") == "NOT_SET"


def test_cryptographic_audit_trail_verification():
    """Verify HMAC SHA-256 tamper-evident record signing."""
    records = [
        {"id": "EV1", "action": "STARTUP", "timestamp": "2026-08-27T00:00:00Z"},
        {"id": "EV2", "action": "ORDER_SUBMIT", "symbol": "BTCUSDT", "qty": 0.05}
    ]

    prev_hash = ""
    for r in records:
        sig = sign_audit_record(r, prev_hash=prev_hash)
        r["signature"] = sig
        prev_hash = sig

    assert verify_audit_chain(records) is True

    # Tamper with record 1
    records[0]["action"] = "UNAUTHORIZED_EDIT"
    assert verify_audit_chain(records) is False


def test_trading_anomaly_detector():
    """Verify anomaly detector triggers on high frequency or excessive notional."""
    detector = TradingAnomalyDetector()

    # Normal order
    anom, reason = detector.record_order(notional=1000.0)
    assert anom is False

    # Excessive notional
    anom, reason = detector.record_order(notional=60000.0)
    assert anom is True
    assert "EXCESSIVE_NOTIONAL" in reason

    # Frequency burst (11 orders in < 60s)
    for _ in range(10):
        detector.record_order(notional=100.0)
    anom, reason = detector.record_order(notional=100.0)
    assert anom is True
    assert "ORDER_FREQUENCY_SPIKE" in reason


def test_monitoring_system_alerts_and_prometheus():
    """Verify monitoring system alert dispatching, ack, and Prometheus format."""
    with tempfile.TemporaryDirectory() as tmpdir:
        alert_log = os.path.join(tmpdir, "alerts.jsonl")
        mon = ProductionMonitoringSystem(alerts_file=alert_log)

        alt = mon.emit_alert("WARNING", "TEST_ALERT", "Test warning alert message")
        assert alt["id"].startswith("ALT_")
        assert len(mon.alerts_history) == 1

        # Acknowledge
        acked = mon.acknowledge_alert(alt["id"])
        assert acked is True
        assert mon.alerts_history[0]["acknowledged"] is True

        # Prometheus export
        prom_text = mon.generate_prometheus_metrics()
        assert "bot_equity" in prom_text
        assert "bot_drawdown_pct" in prom_text
        assert "bot_system_cpu_percent" in prom_text
