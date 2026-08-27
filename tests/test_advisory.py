"""
tests/test_advisory.py — Comprehensive unit & integration tests for the AI-Universe Advisory Subsystem.

Tests covered:
1. AdvisoryGate bounds tests:
   - +20% param change accepted, +21% rejected.
   - Position size 1.5x accepted, 2.0x rejected.
   - Leverage increase rejected, decrease / same accepted.
   - Forbidden params (risk limits, credentials, live trading flags) rejected.
   - Cooldown enforcement (4h cooldown between live changes).
   - Max 2 changes per decision enforcement.
2. AIUniverseClient with mocked HTTP:
   - Success response parsing.
   - Timeout fails soft (returns None).
   - Malformed / non-200 response returns None.
   - Health check endpoint verification.
3. AdvisoryScheduler tests:
   - Exception during consultation is caught, logged, and does not propagate.
   - Full cycle updates ledger and overlay appropriately.
4. AdvisoryLedger atomic write & read tests.
5. AdvisoryParameterOverlay apply / rollback / persistence roundtrip tests.
"""

import datetime
import json
import os
import tempfile
import time
from unittest.mock import MagicMock, patch
import pytest
import requests

from advisory_gate import AdvisoryGate, AdvisoryResult
from advisory_ledger import append_advisory_entry, read_recent_advisory_entries
from advisory_params import AdvisoryParameterOverlay
from advisory_scheduler import AdvisoryScheduler
from advisory_telemetry import build_telemetry_payload
from ai_universe_client import AIUniverseClient


# ==============================================================================
# 1. ADVISORY GATE BOUNDS TESTS
# ==============================================================================

def test_advisory_gate_param_change_pct_bounds():
    """Verify ±20% parameter change limit is strictly enforced."""
    gate = AdvisoryGate()
    current_params = {"adx_threshold": 25.0}

    # Case A: +20% change (25 -> 30) -> PASS
    dec_pass = {
        "decision_id": "DEC_PCT_PASS",
        "status": "APPROVED",
        "confidence": 0.85,
        "parameter_changes": [
            {"parameter": "adx_threshold", "current_value": 25.0, "new_value": 30.0, "strategy": "adx_ema"}
        ]
    }
    res_pass = gate.validate(dec_pass, current_params, shadow_mode=False)
    assert res_pass.verdict == "APPLY"
    assert len(res_pass.applied_changes) == 1
    assert len(res_pass.rejected_changes) == 0

    # Case B: +21% change (25 -> 30.25) -> REJECT
    dec_fail = {
        "decision_id": "DEC_PCT_FAIL",
        "status": "APPROVED",
        "confidence": 0.85,
        "parameter_changes": [
            {"parameter": "adx_threshold", "current_value": 25.0, "new_value": 30.25, "strategy": "adx_ema"}
        ]
    }
    res_fail = gate.validate(dec_fail, current_params, shadow_mode=False)
    assert res_fail.verdict == "REJECT"
    assert len(res_fail.applied_changes) == 0
    assert len(res_fail.rejected_changes) == 1
    assert "exceeds maximum allowed" in res_fail.rejected_changes[0]["reason"]


def test_advisory_gate_position_size_multiplier_bounds():
    """Verify position sizing multipliers are clamped to [0.5x, 1.5x]."""
    gate = AdvisoryGate()
    current_params = {"trade_qty": 0.002}

    # Case A: 1.5x scale (0.002 -> 0.003) -> PASS
    dec_15 = {
        "decision_id": "DEC_SIZE_15",
        "status": "APPROVED",
        "confidence": 0.90,
        "parameter_changes": [
            {"parameter": "trade_qty", "current_value": 0.002, "new_value": 0.003, "strategy": "global"}
        ]
    }
    res_15 = gate.validate(dec_15, current_params, shadow_mode=False)
    assert res_15.verdict == "APPLY"

    # Case B: 2.0x scale (0.002 -> 0.004) -> REJECT
    dec_20 = {
        "decision_id": "DEC_SIZE_20",
        "status": "APPROVED",
        "confidence": 0.90,
        "parameter_changes": [
            {"parameter": "trade_qty", "current_value": 0.002, "new_value": 0.004, "strategy": "global"}
        ]
    }
    res_20 = gate.validate(dec_20, current_params, shadow_mode=False)
    assert res_20.verdict == "REJECT"
    assert "outside allowed bounds" in res_20.rejected_changes[0]["reason"]

    # Case C: 0.4x scale (0.002 -> 0.0008) -> REJECT (too small, <0.5x)
    dec_04 = {
        "decision_id": "DEC_SIZE_04",
        "status": "APPROVED",
        "confidence": 0.90,
        "parameter_changes": [
            {"parameter": "position_size", "current_value": 0.002, "new_value": 0.0008, "strategy": "global"}
        ]
    }
    res_04 = gate.validate(dec_04, current_params, shadow_mode=False)
    assert res_04.verdict == "REJECT"


def test_advisory_gate_leverage_invariants():
    """Verify leverage may only decrease or stay the same, NEVER increase."""
    gate = AdvisoryGate()
    current_params = {"futures_leverage": 5}

    # Case A: Leverage Increase (5x -> 10x) -> REJECT
    dec_inc = {
        "decision_id": "DEC_LEV_INC",
        "status": "APPROVED",
        "confidence": 0.85,
        "parameter_changes": [
            {"parameter": "futures_leverage", "current_value": 5, "new_value": 10, "strategy": "global"}
        ]
    }
    res_inc = gate.validate(dec_inc, current_params, shadow_mode=False)
    assert res_inc.verdict == "REJECT"
    assert "Leverage increase rejected" in res_inc.rejected_changes[0]["reason"]

    # Case B: Leverage Decrease (5x -> 3x) -> PASS
    dec_dec = {
        "decision_id": "DEC_LEV_DEC",
        "status": "APPROVED",
        "confidence": 0.85,
        "parameter_changes": [
            {"parameter": "futures_leverage", "current_value": 5, "new_value": 3, "strategy": "global"}
        ]
    }
    res_dec = gate.validate(dec_dec, current_params, shadow_mode=False)
    assert res_dec.verdict == "APPLY"


def test_advisory_gate_forbidden_params():
    """Verify risk limits, live trading flags, and credentials are auto-rejected."""
    gate = AdvisoryGate()
    current_params = {}

    forbidden_cases = [
        "max_daily_loss",
        "max_drawdown",
        "live_trading_enabled",
        "api_key",
        "secret_key",
        "risk_limits"
    ]

    for param in forbidden_cases:
        dec = {
            "decision_id": f"DEC_FORBIDDEN_{param}",
            "status": "APPROVED",
            "confidence": 0.99,
            "parameter_changes": [
                {"parameter": param, "current_value": 0.05, "new_value": 0.10, "strategy": "global"}
            ]
        }
        res = gate.validate(dec, current_params, shadow_mode=False)
        assert res.verdict == "REJECT"
        assert "FORBIDDEN_PARAMS" in res.rejected_changes[0]["reason"]


def test_advisory_gate_max_changes_limit():
    """Verify maximum 2 parameter changes per decision."""
    gate = AdvisoryGate()
    current_params = {"p1": 10, "p2": 20, "p3": 30}

    dec_3 = {
        "decision_id": "DEC_MAX_3",
        "status": "APPROVED",
        "confidence": 0.88,
        "parameter_changes": [
            {"parameter": "p1", "current_value": 10, "new_value": 11},
            {"parameter": "p2", "current_value": 20, "new_value": 22},
            {"parameter": "p3", "current_value": 30, "new_value": 33}
        ]
    }
    res_3 = gate.validate(dec_3, current_params, shadow_mode=False)
    assert res_3.verdict == "REJECT"
    assert "exceeding maximum limit" in res_3.rationale


def test_advisory_gate_cooldown_and_shadow_mode():
    """Verify 4h cooldown on live execution and SHADOW_LOG_ONLY verdict in shadow mode."""
    gate = AdvisoryGate()
    current_params = {"ema_fast": 20.0}

    dec = {
        "decision_id": "DEC_COOLDOWN",
        "status": "APPROVED",
        "confidence": 0.85,
        "parameter_changes": [
            {"parameter": "ema_fast", "current_value": 20.0, "new_value": 22.0, "strategy": "adx_ema"}
        ]
    }

    # Case A: Shadow Mode ON -> Verdict must be SHADOW_LOG_ONLY
    res_shadow = gate.validate(dec, current_params, shadow_mode=True)
    assert res_shadow.verdict == "SHADOW_LOG_ONLY"
    assert len(res_shadow.applied_changes) == 1

    # Case B: Live Mode with recent last_applied_time (1 hour ago < 4 hours) -> REJECT
    recent_time = datetime.datetime.utcnow() - datetime.timedelta(hours=1)
    res_cooldown = gate.validate(dec, current_params, last_applied_time=recent_time, shadow_mode=False)
    assert res_cooldown.verdict == "REJECT"
    assert "Cooldown in effect" in res_cooldown.rationale

    # Case C: Live Mode with past cooldown (5 hours ago > 4 hours) -> APPLY
    past_time = datetime.datetime.utcnow() - datetime.timedelta(hours=5)
    res_apply = gate.validate(dec, current_params, last_applied_time=past_time, shadow_mode=False)
    assert res_apply.verdict == "APPLY"


# ==============================================================================
# 2. AI UNIVERSE CLIENT TESTS
# ==============================================================================

def test_ai_universe_client_success():
    """Verify AIUniverseClient parses structured decision responses accurately."""
    client = AIUniverseClient(base_url="http://mock-ai:8000")

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "decision_id": "DEC_12345",
        "status": "APPROVED",
        "confidence": 0.92,
        "parameter_changes": [
            {"strategy": "aggressive_scalper", "parameter": "sl_pct", "current_value": 0.005, "new_value": 0.004}
        ],
        "debate_summary": "Volatility elevated; tighten SL"
    }

    with patch.object(client.session, "post", return_value=mock_resp):
        decision = client.consult({"mock": "telemetry"})
        assert decision is not None
        assert decision["decision_id"] == "DEC_12345"
        assert decision["confidence"] == 0.92
        assert len(decision["parameter_changes"]) == 1


def test_ai_universe_client_timeout_fails_soft():
    """Verify request timeout returns None without raising an exception."""
    client = AIUniverseClient(base_url="http://mock-ai:8000", timeout=1)

    with patch.object(client.session, "post", side_effect=requests.exceptions.Timeout("Timed out")):
        res = client.consult({"mock": "telemetry"})
        assert res is None


def test_ai_universe_client_malformed_response_fails_soft():
    """Verify missing required fields or non-JSON payloads return None."""
    client = AIUniverseClient(base_url="http://mock-ai:8000")

    # Missing confidence & parameter_changes
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"decision_id": "DEC_BAD", "status": "APPROVED"}

    with patch.object(client.session, "post", return_value=mock_resp):
        res = client.consult({"mock": "telemetry"})
        assert res is None


def test_ai_universe_client_health_check():
    """Verify health_check returns True on 200 and False on failure."""
    client = AIUniverseClient(base_url="http://mock-ai:8000")

    mock_ok = MagicMock()
    mock_ok.status_code = 200
    with patch.object(client.session, "get", return_value=mock_ok):
        assert client.health_check() is True

    with patch.object(client.session, "get", side_effect=requests.exceptions.ConnectionError("Offline")):
        assert client.health_check() is False


# ==============================================================================
# 3. ADVISORY LEDGER & ATOMIC WRITE TESTS
# ==============================================================================

def test_advisory_ledger_append_and_read():
    """Verify JSONL append and read in reverse chronological order."""
    with tempfile.NamedTemporaryFile(suffix=".jsonl", delete=False) as tf:
        temp_log_path = tf.name

    try:
        # Append 3 records
        for i in range(3):
            append_advisory_entry({
                "decision_id": f"DEC_{i}",
                "consultation_reason": "SCHEDULED",
                "ai_status": "APPROVED",
                "confidence": 0.8 + (i * 0.05),
                "verdict": "APPLY" if i % 2 == 0 else "REJECT"
            }, filepath=temp_log_path)

        entries = read_recent_advisory_entries(limit=10, filepath=temp_log_path)
        assert len(entries) == 3
        # Most recent first
        assert entries[0]["decision_id"] == "DEC_2"
        assert entries[1]["decision_id"] == "DEC_1"
        assert entries[2]["decision_id"] == "DEC_0"
    finally:
        if os.path.exists(temp_log_path):
            os.remove(temp_log_path)


# ==============================================================================
# 4. ADVISORY PARAMETER OVERLAY TESTS
# ==============================================================================

def test_advisory_parameter_overlay_lifecycle():
    """Verify overlay apply, get_param, rollback, and file persistence roundtrip."""
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as tf:
        temp_state_path = tf.name

    try:
        overlay = AdvisoryParameterOverlay(state_file=temp_state_path)

        # Default query
        assert overlay.get_param("adx_ema", "sl_atr_multiplier", default=2.0) == 2.0

        # Apply change 1
        changes_1 = [
            {"strategy": "adx_ema", "parameter": "sl_atr_multiplier", "current_value": 2.0, "new_value": 2.4, "reason": "Test"}
        ]
        overlay.apply_changes("DEC_BATCH_1", changes_1)
        assert overlay.get_param("adx_ema", "sl_atr_multiplier") == 2.4

        # Verify state persistence by creating a fresh overlay instance reading same file
        overlay_reloaded = AdvisoryParameterOverlay(state_file=temp_state_path)
        assert overlay_reloaded.get_param("adx_ema", "sl_atr_multiplier") == 2.4

        # Rollback change 1
        rolled_back = overlay_reloaded.rollback("DEC_BATCH_1")
        assert rolled_back is True
        assert overlay_reloaded.get_param("adx_ema", "sl_atr_multiplier", default=2.0) == 2.0

    finally:
        if os.path.exists(temp_state_path):
            os.remove(temp_state_path)


# ==============================================================================
# 5. ADVISORY SCHEDULER TESTS
# ==============================================================================

def test_advisory_scheduler_exception_does_not_propagate():
    """Verify exceptions in consultation cycle are caught, logged, and return None."""
    mock_client = MagicMock(spec=AIUniverseClient)
    mock_client.consult.side_effect = RuntimeError("Fatal AI Universe internal bug")

    scheduler = AdvisoryScheduler(client=mock_client, shadow_mode=True)
    res = scheduler.run_consultation_cycle(reason="UNIT_TEST")
    assert res is None  # Fails soft without crashing caller


def test_advisory_scheduler_full_cycle_shadow_mode():
    """Verify full consultation cycle runs in shadow mode without live overlay updates."""
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as tf_state, \
         tempfile.NamedTemporaryFile(suffix=".jsonl", delete=False) as tf_log:
        temp_state = tf_state.name
        temp_log = tf_log.name

    try:
        mock_client = MagicMock(spec=AIUniverseClient)
        mock_client.consult.return_value = {
            "decision_id": "DEC_CYCLE_TEST",
            "status": "APPROVED",
            "confidence": 0.90,
            "parameter_changes": [
                {"strategy": "adx_ema", "parameter": "adx_threshold", "current_value": 25, "new_value": 28}
            ],
            "debate_summary": "Strong consensus"
        }

        with patch("advisory_ledger.ADVISORY_LOG_FILE", temp_log):
            scheduler = AdvisoryScheduler(client=mock_client, shadow_mode=True)
            res = scheduler.run_consultation_cycle(reason="SCHEDULED")

            assert res is not None
            assert res["decision_id"] == "DEC_CYCLE_TEST"
            assert res["verdict"] == "SHADOW_LOG_ONLY"
            assert res["shadow_mode"] is True

    finally:
        if os.path.exists(temp_state): os.remove(temp_state)
        if os.path.exists(temp_log): os.remove(temp_log)
