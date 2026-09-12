"""
tests/test_prompt8_stratex.py — Comprehensive acceptance and regression test suite for Prompt 8: Stratex.

Verifies:
1. Stratex can run in paper mode with no external order.
2. Stratex can run against Binance testnet only when explicitly configured.
3. Inference can provide an advisory but cannot execute an order.
4. FRIDAY can inspect status and request panic.
5. Invalid or unsafe recommendations are rejected deterministically.
6. No live-money path can be enabled accidentally by an environment typo.
7. Telemetry freshness, exchange connectivity, position reconciliation, and clock-skew checks.
8. Bounded recommendations requiring 8 mandatory fields.
9. Advisory application is separate from advisory generation.
10. Explicit authorization required for parameter changes.
11. Preserved panic and emergency kill-switch behavior.
12. Idempotency for order and parameter-change requests.
13. Tamper-evident cryptographic SHA-256 audit logging.
"""

import datetime
import json
import os
import time
from unittest import mock
from unittest.mock import MagicMock, patch

import pytest

import config
from advisory_gate import AdvisoryGate, BoundedRecommendation
from advisory_params import AdvisoryParameterOverlay
from ai_universe_client import AIUniverseClient
from audit.audit_manager import AuditManager, IdempotencyStore, get_audit_manager
from dashboard import app
import execution
from execution import ExecutionPolicy
from telemetry.health_guard import (
    ClockSkewError,
    ExchangeOutageError,
    ReconciliationMismatchError,
    StaleTelemetryError,
    check_clock_skew,
    check_exchange_connectivity,
    check_telemetry_freshness,
    reconcile_positions,
)


@pytest.fixture
def client():
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c


# ==============================================================================
# 1. PAPER MODE ZERO EXTERNAL ORDERS
# ==============================================================================

def test_paper_mode_runs_with_no_external_orders(monkeypatch):
    """Verify Stratex runs in paper mode without dispatching any external orders to Binance."""
    monkeypatch.setattr(config, "TRADING_MODE", "PAPER")
    monkeypatch.setattr(config, "PAPER_SAFE_MODE", True)
    monkeypatch.setattr(config, "LIVE_TRADING_ENABLED", False)

    # 1. ExecutionPolicy must block external orders
    allowed, reason = ExecutionPolicy.can_place_order()
    assert allowed is False
    assert "PAPER_BLOCKED" in reason

    # 2. get_exchange_client() must return None
    exc_client = execution.get_exchange_client()
    assert exc_client is None

    # 3. Direct market order attempt in paper mode raises RuntimeError
    with pytest.raises(RuntimeError, match="PAPER mode attempted to place a real Binance order"):
        execution.place_market_order(
            strategy_name="adx_ema",
            side="BUY",
            symbol="BTCUSDT",
            quantity=0.001
        )


# ==============================================================================
# 2. BINANCE TESTNET EXPLICIT CONFIGURATION ONLY
# ==============================================================================

def test_binance_testnet_explicit_config_only(monkeypatch):
    """Verify Stratex only executes on Binance testnet when explicitly enabled with credentials."""
    # Case A: TESTNET mode but TESTNET_ENABLED is False -> Blocked
    monkeypatch.setattr(config, "TRADING_MODE", "TESTNET")
    monkeypatch.setattr(config, "TESTNET_ENABLED", False)
    monkeypatch.setattr(config, "PAPER_SAFE_MODE", False)

    allowed, reason = ExecutionPolicy.can_place_order()
    assert allowed is False
    assert reason == "TESTNET_DISABLED"

    with pytest.raises(RuntimeError, match="TESTNET execution attempted but TESTNET_ENABLED is false"):
        execution.get_exchange_client()

    # Case B: Explicitly configured TESTNET
    monkeypatch.setattr(config, "TESTNET_ENABLED", True)
    monkeypatch.setattr(config, "API_KEY", "testnet_dummy_key")
    monkeypatch.setattr(config, "SECRET_KEY", "testnet_dummy_secret")

    allowed, reason = ExecutionPolicy.can_place_order()
    assert allowed is True
    assert reason == "ALLOWED_TESTNET"

    with mock.patch("binance.client.Client.ping"):
        cl = execution.get_exchange_client()
        assert cl is not None
        assert getattr(cl, "testnet", False) is True


# ==============================================================================
# 3. NO LIVE-MONEY PATH VIA ENVIRONMENT TYPOS
# ==============================================================================

@pytest.mark.parametrize("env_key,env_val", [
    ("LIVE_TRADING", "true"),
    ("LIVE_TRADING_ENABLED", "True"),
    ("ENABLE_LIVE", "1"),
    ("REAL_MONEY", "yes"),
    ("PROD_TRADING", "enabled"),
    ("PRODUCTION_MONEY", "true"),
    ("BASE_URL", "https://api.binance.com"),
    ("FUTURES_BASE_URL", "https://fapi.binance.com")
])
def test_no_live_money_path_on_environment_typo(env_key, env_val):
    """Verify that any environment typo or attempt to enable live trading fails closed."""
    with patch.dict(os.environ, {env_key: env_val}):
        with pytest.raises(ValueError, match="SECURITY CRITICAL"):
            config.validate_environment_safety()


def test_invalid_trading_mode_fails_closed(monkeypatch):
    """Verify invalid TRADING_MODE (like 'LIVE', 'REAL', 'PROD') fails closed."""
    monkeypatch.setattr(config, "TRADING_MODE", "LIVE")
    with pytest.raises(ValueError, match="Invalid TRADING_MODE"):
        config.validate_config()


# ==============================================================================
# 4. INFERENCE ADVISORY CANNOT EXECUTE ORDERS
# ==============================================================================

def test_inference_advisory_cannot_execute_orders():
    """Verify that Inference is strictly advisory and cannot execute orders or return executable directives."""
    client = AIUniverseClient(base_url="http://mock-inference:8000")

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    # Response contains an advisory recommendation AND an unauthorized executable order payload
    mock_resp.json.return_value = {
        "decision_id": "dec_adv_1",
        "status": "APPROVED",
        "confidence": 0.88,
        "parameter_changes": [
            {
                "parameter": "adx_period",
                "strategy": "adx_ema",
                "current_value": 14.0,
                "proposed_value": 16.0,
                "maximum_delta": 2.5,
                "evidence": "ATR expansion",
                "confidence": 0.85,
                "expiry": (datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=2)).isoformat(),
                "authorization_required": True
            }
        ],
        "orders": [{"symbol": "BTCUSDT", "side": "BUY", "quantity": 10.0}],
        "execute": True
    }

    with patch.object(client.session, "post", return_value=mock_resp):
        data = client.consult({"mock": "telemetry"})
        assert data is not None
        # Verify executable orders were stripped
        assert "orders" not in data
        assert "execute" not in data
        assert len(data["parameter_changes"]) == 1


def test_universal_task_endpoint_rejects_external_order_execution(client):
    """Verify that /v1/task/execute rejects external agent attempts to execute orders with HTTP 403."""
    payload = {
        "task_id": "task_external_order",
        "source_agent": "inference",
        "target_agent": "stratex",
        "action": "execute_order",
        "payload": {"symbol": "BTCUSDT", "side": "BUY"}
    }
    res = client.post("/v1/task/execute", json=payload)
    assert res.status_code == 403
    data = res.get_json()
    assert data["status"] == "FORBIDDEN"
    assert "DETERMINISTIC_EXECUTION_AUTHORITY_VIOLATION" in data["error"]


def test_safety_gates_cannot_be_bypassed_by_friday_or_inference(client):
    """Verify external agents cannot bypass safety gates."""
    payload = {
        "task_id": "task_bypass",
        "source_agent": "friday",
        "target_agent": "stratex",
        "action": "bypass_gates",
        "payload": {"force": True}
    }
    res = client.post("/v1/task/execute", json=payload)
    assert res.status_code == 403


# ==============================================================================
# 5. STANDARDIZED TRADING CONSULT CONTRACT
# ==============================================================================

def test_standardized_trading_consult_contract():
    """Verify the standardized /v1/trading/consult request shape."""
    client = AIUniverseClient(base_url="http://mock-inference:8000")
    raw_telemetry = {
        "active_strategy": "adx_ema",
        "portfolio": {"equity": 10500.0, "open_positions_count": 1}
    }
    std = client.standardize_consult_payload(raw_telemetry)
    assert "telemetry_freshness" in std
    assert "exchange_status" in std
    assert "positions" in std
    assert std["telemetry_freshness"]["max_staleness_seconds"] == 60.0


# ==============================================================================
# 6. BOUNDED RECOMMENDATIONS MANDATORY 8 FIELDS
# ==============================================================================

def test_bounded_recommendations_mandatory_8_fields():
    """Verify 8 mandatory fields: parameter, current_value, proposed_value, maximum_delta, evidence, confidence, expiry, authorization_required."""
    gate = AdvisoryGate()
    future_expiry = (datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=2)).isoformat()

    valid_rec = {
        "parameter": "adx_period",
        "current_value": 14.0,
        "proposed_value": 16.0,
        "maximum_delta": 2.8,
        "evidence": "ATR expansion in 4h candle regime",
        "confidence": 0.85,
        "expiry": future_expiry,
        "authorization_required": True,
        "strategy": "adx_ema"
    }
    ok, reason, _ = gate.validate_bounded_recommendation(valid_rec)
    assert ok is True

    # Missing evidence
    bad_rec_1 = dict(valid_rec)
    bad_rec_1["evidence"] = ""
    ok, reason, _ = gate.validate_bounded_recommendation(bad_rec_1)
    assert ok is False
    assert "Evidence" in reason

    # Missing authorization_required
    bad_rec_2 = dict(valid_rec)
    del bad_rec_2["authorization_required"]
    ok, reason, _ = gate.validate_bounded_recommendation(bad_rec_2)
    assert ok is False
    assert "authorization_required" in reason

    # authorization_required is False
    bad_rec_3 = dict(valid_rec)
    bad_rec_3["authorization_required"] = False
    ok, reason, _ = gate.validate_bounded_recommendation(bad_rec_3)
    assert ok is False
    assert "strictly True" in reason

    # Already expired
    bad_rec_4 = dict(valid_rec)
    bad_rec_4["expiry"] = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(minutes=10)).isoformat()
    ok, reason, _ = gate.validate_bounded_recommendation(bad_rec_4)
    assert ok is False
    assert "expired" in reason.lower()


# ==============================================================================
# 7. ADVISORY REJECTION CONDITIONS
# ==============================================================================

def test_advisory_rejection_conditions():
    """Verify rejection on insufficient data, stale telemetry, excessive drawdown, and inconsistent state."""
    gate = AdvisoryGate()
    current_params = {"adx_period": 14.0}
    dec = {
        "decision_id": "DEC_REJECT_TEST",
        "status": "APPROVED",
        "confidence": 0.85,
        "parameter_changes": [{"parameter": "adx_period", "current_value": 14.0, "new_value": 16.0}]
    }

    # 1. Stale telemetry (> 60s)
    res_stale = gate.validate(dec, current_params, telemetry_freshness_sec=95.0)
    assert res_stale.verdict == "REJECT"
    assert "stale" in res_stale.rationale.lower()

    # 2. Insufficient data (< 10 trades)
    res_data = gate.validate(dec, current_params, trade_count=4)
    assert res_data.verdict == "REJECT"
    assert "insufficient data" in res_data.rationale.lower()

    # 3. Excessive drawdown (> 15%)
    res_dd = gate.validate(dec, current_params, current_drawdown_pct=18.5)
    assert res_dd.verdict == "REJECT"
    assert "drawdown" in res_dd.rationale.lower()

    # 4. Inconsistent state
    res_incon = gate.validate(dec, current_params, inconsistent_state=True)
    assert res_incon.verdict == "REJECT"
    assert "inconsistent" in res_incon.rationale.lower()

    # 5. Maximum delta violation
    dec_delta = {
        "decision_id": "DEC_DELTA_VIOLATION",
        "status": "APPROVED",
        "confidence": 0.85,
        "parameter_changes": [
            {
                "parameter": "adx_period",
                "current_value": 14.0,
                "proposed_value": 16.0,
                "maximum_delta": 1.0  # Proposed change is 2.0 > 1.0
            }
        ]
    }
    res_delta = gate.validate(dec_delta, current_params)
    assert res_delta.verdict == "REJECT"


# ==============================================================================
# 8. ADVISORY APPLICATION SEPARATE FROM GENERATION & EXPLICIT AUTHORIZATION
# ==============================================================================

def test_advisory_application_separate_from_generation_and_authorization(tmp_path):
    """Verify that staging an advisory recommendation does not modify overlay until explicitly authorized."""
    state_file = str(tmp_path / "overlay_test.json")
    overlay = AdvisoryParameterOverlay(state_file=state_file)

    future_exp = (datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=2)).isoformat()
    rec = {
        "parameter": "adx_period",
        "strategy": "adx_ema",
        "current_value": 14.0,
        "proposed_value": 16.0,
        "maximum_delta": 2.5,
        "evidence": "Volatility breakout",
        "confidence": 0.85,
        "expiry": future_exp,
        "authorization_required": True
    }

    # Step 1: Stage recommendation
    rec_id = overlay.stage_recommendation(rec)
    assert rec_id is not None
    assert len(overlay.get_pending_recommendations()) == 1

    # Invariant: Active parameter is UNCHANGED
    assert overlay.get_param("adx_ema", "adx_period", default=14.0) == 14.0

    # Step 2: Attempt authorization without token -> Fails
    ok, msg, _ = overlay.apply_authorized_recommendation(rec_id, authorization_token="", authorized_by="FRIDAY")
    assert ok is False
    assert overlay.get_param("adx_ema", "adx_period", default=14.0) == 14.0

    # Step 3: Authorize with valid token -> Succeeds
    ok, msg, res = overlay.apply_authorized_recommendation(
        recommendation_id=rec_id,
        authorization_token="friday_sec_token_999",
        authorized_by="FRIDAY"
    )
    assert ok is True
    assert overlay.get_param("adx_ema", "adx_period") == 16.0
    assert len(overlay.get_pending_recommendations()) == 0


# ==============================================================================
# 9. FRIDAY SUPERVISION ENDPOINTS & PANIC BEHAVIOR
# ==============================================================================

def test_friday_supervision_endpoints_and_panic(client):
    """Verify FRIDAY supervision status inspection and panic halt behavior."""
    # 1. GET /v1/friday/supervision/status
    res = client.get("/v1/friday/supervision/status")
    assert res.status_code == 200
    data = res.get_json()["data"]
    assert data["engine"] == "Stratex"
    assert data["live_trading_enabled"] is False
    assert data["live_money_permanently_blocked"] is True

    # 2. POST /v1/friday/supervision/panic without confirmation -> 400 Bad Request
    res_bad = client.post("/v1/friday/supervision/panic", json={})
    assert res_bad.status_code == 400

    # 3. POST /v1/friday/supervision/panic with confirmation -> 200 OK & Panic Active
    res_panic = client.post("/v1/friday/supervision/panic", json={
        "confirm": True,
        "reason": "Test panic",
        "source": "FRIDAY"
    })
    assert res_panic.status_code == 200
    assert res_panic.get_json()["panic_active"] is True

    # 4. Verify orders are blocked when panic is active
    with pytest.raises(RuntimeError, match="Emergency Panic Kill-Switch is active"):
        execution._check_panic_and_kill_switch()

    # 5. Release panic
    res_release = client.post("/v1/friday/supervision/panic", json={
        "confirm": True,
        "release": True,
        "reason": "Clear test panic"
    })
    assert res_release.status_code == 200
    assert res_release.get_json()["panic_active"] is False


# ==============================================================================
# 10. IDEMPOTENCY FOR ORDERS AND PARAMETER CHANGES
# ==============================================================================

def test_order_and_parameter_idempotency(tmp_path):
    """Verify idempotency deduplication prevents duplicate orders or parameter changes."""
    store_file = str(tmp_path / "idemp_test.json")
    store = IdempotencyStore(store_path=store_file)

    key = "idemp_order_12345"
    is_dup, rec = store.check_and_record(key, request_type="ORDER")
    assert is_dup is False
    assert rec["status"] == "PENDING"

    # Simulate completed order
    store.complete_request(key, {"orderId": "ORD_999", "status": "FILLED"})

    # Second check must detect duplicate and return cached response
    is_dup_2, cached = store.check_and_record(key, request_type="ORDER")
    assert is_dup_2 is True
    assert cached["response"]["orderId"] == "ORD_999"


# ==============================================================================
# 11. TELEMETRY FRESHNESS AND CLOCK SKEW CHECKS
# ==============================================================================

def test_telemetry_freshness_and_clock_skew_checks():
    """Verify health guard checks for telemetry staleness and clock skew."""
    now = time.time()

    # Fresh timestamp (10s old) -> Passes
    fresh_age = check_telemetry_freshness(now - 10.0, max_age_seconds=60.0)
    assert 9.0 <= fresh_age <= 12.0

    # Stale timestamp (120s old) -> Raises StaleTelemetryError
    with pytest.raises(StaleTelemetryError, match="Telemetry is stale"):
        check_telemetry_freshness(now - 120.0, max_age_seconds=60.0)

    # Missing timestamp -> Raises StaleTelemetryError
    with pytest.raises(StaleTelemetryError, match="missing or None"):
        check_telemetry_freshness(None)

    # Acceptable clock skew (50ms) -> Passes
    skew = check_clock_skew(exchange_server_time_ms=(now * 1000) + 50, local_time_ms=(now * 1000))
    assert skew == 50.0

    # Excessive clock skew (2500ms > 1000ms) -> Raises ClockSkewError
    with pytest.raises(ClockSkewError, match="Clock skew violation"):
        check_clock_skew(exchange_server_time_ms=(now * 1000) + 2500, local_time_ms=(now * 1000))


# ==============================================================================
# 12. EXCHANGE OUTAGE AND RECONCILIATION MISMATCH
# ==============================================================================

def test_exchange_outage_and_reconciliation_mismatch():
    """Verify exchange outage detection and position reconciliation mismatch halts."""
    # Outage on None client
    with pytest.raises(ExchangeOutageError, match="client is None"):
        check_exchange_connectivity(None)

    # Outage on failing client
    broken_client = MagicMock()
    broken_client.ping.side_effect = ConnectionError("Socket reset by peer")
    with pytest.raises(ExchangeOutageError, match="Socket reset"):
        check_exchange_connectivity(broken_client)

    # Position reconciliation match
    int_pos = {"BTCUSDT": {"quantity": 0.05, "direction": "BUY"}}
    exc_pos = [{"symbol": "BTCUSDT", "positionAmt": 0.05}]
    res = reconcile_positions(int_pos, exc_pos)
    assert res["status"] == "RECONCILED"

    # Position reconciliation mismatch (0.05 vs 0.15)
    exc_pos_mismatch = [{"symbol": "BTCUSDT", "positionAmt": 0.15}]
    with pytest.raises(ReconciliationMismatchError, match="reconciliation mismatch"):
        reconcile_positions(int_pos, exc_pos_mismatch)


# ==============================================================================
# 13. CRYPTOGRAPHIC SHA-256 AUDIT LOGGING
# ==============================================================================

def test_cryptographic_audit_logging_and_tamper_detection(tmp_path):
    """Verify SHA-256 hash chaining and tamper detection in AuditManager."""
    log_file = str(tmp_path / "audit_test.jsonl")
    audit = AuditManager(log_path=log_file)

    # Record two sequential events
    ev1 = audit.record_event(
        event_type="RECOMMENDATION_GENERATED",
        actor="inference",
        details={"decision_id": "dec_1"}
    )
    assert ev1["prev_hash"] == "0" * 64
    assert len(ev1["hash"]) == 64

    ev2 = audit.record_event(
        event_type="RECOMMENDATION_AUTHORIZED",
        actor="FRIDAY",
        details={"decision_id": "dec_1"}
    )
    assert ev2["prev_hash"] == ev1["hash"]

    # Verify chain integrity -> True
    valid, count, msg = audit.verify_log_integrity()
    assert valid is True
    assert count == 2

    # Tamper with the first record in the file
    with open(log_file, "r", encoding="utf-8") as f:
        lines = f.readlines()

    tampered_rec = json.loads(lines[0])
    tampered_rec["actor"] = "MALICIOUS_ATTACKER"
    lines[0] = json.dumps(tampered_rec) + "\n"

    with open(log_file, "w", encoding="utf-8") as f:
        f.writelines(lines)

    # Integrity verification must detect tampering fail-closed
    valid_tampered, _, tamper_msg = audit.verify_log_integrity()
    assert valid_tampered is False
    assert "mismatch" in tamper_msg.lower() or "broken" in tamper_msg.lower()
