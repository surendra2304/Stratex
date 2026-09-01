"""
tests/test_advisory_bounds.py — Explicit verification of hardcoded safety bounds in advisory_gate.py.

Verifications:
1. Parameter change > 20% -> REJECT
2. Position size > 1.5x current -> REJECT
3. Position size < 0.5x current -> REJECT
4. Leverage increase -> REJECT
5. Forbidden parameters (risk_limits, max_daily_loss, max_drawdown, live_trading_enabled, api_key) -> REJECT
6. Cooldown period violation (< 4 hours) -> REJECT
7. Maximum 2 changes per decision -> REJECT
8. Confirmation that bounds are hardcoded constants on AdvisoryGate class and cannot be overridden by environment variables.
"""

import datetime
import os
from unittest.mock import patch

from advisory_gate import AdvisoryGate


def test_bounds_are_hardcoded_constants_unaffected_by_env():
    """Verify that AdvisoryGate bounds are immutable class attributes and ignore environment modifications."""
    with patch.dict(os.environ, {
        "MAX_PARAM_CHANGE_PCT": "999",
        "POSITION_SIZE_MAX_MULT": "999",
        "COOLDOWN_HOURS": "0"
    }):
        gate = AdvisoryGate()
        assert gate.MAX_PARAM_CHANGE_PCT == 20.0
        assert gate.POSITION_SIZE_MIN_MULT == 0.5
        assert gate.POSITION_SIZE_MAX_MULT == 1.5
        assert gate.MAX_CHANGES_PER_DECISION == 2
        assert gate.COOLDOWN_HOURS == 4.0
        assert "max_daily_loss" in gate.FORBIDDEN_PARAMS
        assert "live_trading_enabled" in gate.FORBIDDEN_PARAMS


def test_parameter_change_greater_than_20_percent_rejected():
    """Verify any parameter delta exceeding ±20% is rejected."""
    gate = AdvisoryGate()
    current_params = {"adx_period": 14.0}

    # 1. Delta = +20.0% (14.0 -> 16.8) -> ACCEPTED
    dec_ok = {
        "decision_id": "DEC_DELTA_20",
        "status": "APPROVED",
        "confidence": 0.85,
        "parameter_changes": [{"parameter": "adx_period", "current_value": 14.0, "new_value": 16.8}]
    }
    res_ok = gate.validate(dec_ok, current_params, shadow_mode=False)
    assert res_ok.verdict == "APPLY"
    assert len(res_ok.applied_changes) == 1

    # 2. Delta = +20.01% (14.0 -> 16.81) -> REJECTED
    dec_bad = {
        "decision_id": "DEC_DELTA_21",
        "status": "APPROVED",
        "confidence": 0.85,
        "parameter_changes": [{"parameter": "adx_period", "current_value": 14.0, "new_value": 16.81}]
    }
    res_bad = gate.validate(dec_bad, current_params, shadow_mode=False)
    assert res_bad.verdict == "REJECT"
    assert "exceeds maximum allowed" in res_bad.rejected_changes[0]["reason"]


def test_position_size_multiplier_bounds():
    """Verify position size multipliers strictly clamped to [0.5x, 1.5x]."""
    gate = AdvisoryGate()
    current_params = {"trade_qty": 0.01}

    # 1.5x -> ACCEPT
    res_15 = gate.validate({
        "decision_id": "DEC_SZ_15",
        "status": "APPROVED",
        "confidence": 0.9,
        "parameter_changes": [{"parameter": "trade_qty", "current_value": 0.01, "new_value": 0.015}]
    }, current_params, shadow_mode=False)
    assert res_15.verdict == "APPLY"

    # 1.51x -> REJECT
    res_151 = gate.validate({
        "decision_id": "DEC_SZ_151",
        "status": "APPROVED",
        "confidence": 0.9,
        "parameter_changes": [{"parameter": "trade_qty", "current_value": 0.01, "new_value": 0.0151}]
    }, current_params, shadow_mode=False)
    assert res_151.verdict == "REJECT"
    assert "outside allowed bounds" in res_151.rejected_changes[0]["reason"]

    # 0.49x -> REJECT
    res_049 = gate.validate({
        "decision_id": "DEC_SZ_049",
        "status": "APPROVED",
        "confidence": 0.9,
        "parameter_changes": [{"parameter": "trade_qty", "current_value": 0.01, "new_value": 0.0049}]
    }, current_params, shadow_mode=False)
    assert res_049.verdict == "REJECT"
    assert "outside allowed bounds" in res_049.rejected_changes[0]["reason"]


def test_leverage_increase_rejected_decrease_accepted():
    """Verify leverage may only decrease or stay the same, NEVER increase."""
    gate = AdvisoryGate()
    current_params = {"leverage": 5}

    # Increase from 5x to 6x -> REJECT
    res_inc = gate.validate({
        "decision_id": "DEC_LEV_INC",
        "status": "APPROVED",
        "confidence": 0.8,
        "parameter_changes": [{"parameter": "leverage", "current_value": 5, "new_value": 6}]
    }, current_params, shadow_mode=False)
    assert res_inc.verdict == "REJECT"
    assert "Leverage increase rejected" in res_inc.rejected_changes[0]["reason"]

    # Decrease from 5x to 3x -> APPLY
    res_dec = gate.validate({
        "decision_id": "DEC_LEV_DEC",
        "status": "APPROVED",
        "confidence": 0.8,
        "parameter_changes": [{"parameter": "leverage", "current_value": 5, "new_value": 3}]
    }, current_params, shadow_mode=False)
    assert res_dec.verdict == "APPLY"


def test_forbidden_parameters_rejected():
    """Verify risk limits and live trading flags are unconditionally rejected."""
    gate = AdvisoryGate()
    current_params = {}

    forbidden_list = [
        "max_daily_loss",
        "max_daily_loss_pct",
        "max_drawdown",
        "max_testnet_drawdown_pct",
        "live_trading_enabled",
        "api_key",
        "secret_key",
        "risk_limits",
        "trading_mode"
    ]

    for param in forbidden_list:
        res = gate.validate({
            "decision_id": f"DEC_FORB_{param}",
            "status": "APPROVED",
            "confidence": 0.95,
            "parameter_changes": [{"parameter": param, "current_value": 1.0, "new_value": 2.0}]
        }, current_params, shadow_mode=False)
        assert res.verdict == "REJECT"
        assert "FORBIDDEN_PARAMS" in res.rejected_changes[0]["reason"]


def test_cooldown_period_enforcement():
    """Verify 4h cooldown between live applied changes."""
    gate = AdvisoryGate()
    current_params = {"ema_fast": 20.0}

    dec = {
        "decision_id": "DEC_COOLDOWN_TEST",
        "status": "APPROVED",
        "confidence": 0.9,
        "parameter_changes": [{"parameter": "ema_fast", "current_value": 20.0, "new_value": 22.0}]
    }

    # 3.5 hours elapsed -> REJECT
    recent_time = datetime.datetime.utcnow() - datetime.timedelta(hours=3.5)
    res_cool = gate.validate(dec, current_params, last_applied_time=recent_time, shadow_mode=False)
    assert res_cool.verdict == "REJECT"
    assert "Cooldown in effect" in res_cool.rationale

    # 4.1 hours elapsed -> APPLY
    ok_time = datetime.datetime.utcnow() - datetime.timedelta(hours=4.1)
    res_ok = gate.validate(dec, current_params, last_applied_time=ok_time, shadow_mode=False)
    assert res_ok.verdict == "APPLY"


def test_max_changes_per_decision_limit():
    """Verify exceeding 2 parameter changes rejects the decision batch."""
    gate = AdvisoryGate()
    current_params = {"p1": 10, "p2": 20, "p3": 30}

    dec = {
        "decision_id": "DEC_BATCH_3",
        "status": "APPROVED",
        "confidence": 0.9,
        "parameter_changes": [
            {"parameter": "p1", "current_value": 10, "new_value": 11},
            {"parameter": "p2", "current_value": 20, "new_value": 22},
            {"parameter": "p3", "current_value": 30, "new_value": 33}
        ]
    }
    res = gate.validate(dec, current_params, shadow_mode=False)
    assert res.verdict == "REJECT"
    assert "exceeding maximum limit of 2" in res.rationale
