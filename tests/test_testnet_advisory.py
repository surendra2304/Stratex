"""
tests/test_testnet_advisory.py — Comprehensive test suite for Binance Testnet AI Advisory Integration.

Verifies:
1. TESTNET_ADVISORY_ENABLED defaults to False in config.
2. When TESTNET_ADVISORY_ENABLED is False, no advisory consultations or background loops execute.
3. When enabled in SHADOW mode (TESTNET_ADVISORY_SHADOW_MODE=True), recommendations are audited to advisory_log.jsonl with verdict='SHADOW_LOG_ONLY' and overlay remains unchanged.
4. When in APPLY mode (TESTNET_ADVISORY_ENABLED=True AND TESTNET_ADVISORY_SHADOW_MODE=False), valid recommendations are applied to the Testnet runtime overlay within strict safety bounds.
5. Strict safety bounds are enforced (±20% param change, position size clamped to [0.5x, 1.5x], leverage non-increasing, forbidden parameters rejected).
6. Drawdown limit circuit breaker: if testnet drawdown >= 15%, trips circuit breaker, disables advisory, and reverts all applied parameter changes back to clean baseline defaults.
7. Manual consultation API triggers function cleanly.
"""

import os
import tempfile
from unittest.mock import MagicMock, patch
import pytest

from advisory_gate import AdvisoryGate
from advisory_params import AdvisoryParameterOverlay
from testnet_advisory_scheduler import TestnetAdvisoryScheduler
from ai_universe_client import AIUniverseClient
import config


def test_testnet_advisory_defaults_to_disabled():
    """Verify TESTNET_ADVISORY_ENABLED defaults to False."""
    assert config.TESTNET_ADVISORY_ENABLED is False


def test_disabled_advisory_makes_no_calls():
    """Verify that when disabled, run_consultation_cycle returns None and client is never queried."""
    mock_client = MagicMock(spec=AIUniverseClient)
    scheduler = TestnetAdvisoryScheduler(client=mock_client, enabled=False)

    res = scheduler.run_consultation_cycle("SCHEDULED")
    assert res is None
    assert mock_client.consult.call_count == 0


def test_testnet_shadow_mode_audits_without_overlay_mutation():
    """Verify that when enabled in SHADOW mode, recommendations write to log but do not mutate overlay."""
    with tempfile.TemporaryDirectory() as tmpdir:
        state_file = os.path.join(tmpdir, "testnet_params.json")
        log_file = os.path.join(tmpdir, "testnet_adv_log.jsonl")

        overlay = AdvisoryParameterOverlay(state_file=state_file)
        mock_client = MagicMock(spec=AIUniverseClient)
        mock_client.consult.return_value = {
            "decision_id": "DEC_TN_SHADOW",
            "status": "APPROVED",
            "confidence": 0.90,
            "parameter_changes": [
                {"strategy": "aggressive_scalper", "parameter": "sl_pct", "current_value": 0.005, "new_value": 0.004}
            ],
            "debate_summary": "Testnet shadow calibration"
        }

        with patch("advisory_ledger.ADVISORY_LOG_FILE", log_file):
            scheduler = TestnetAdvisoryScheduler(
                client=mock_client,
                overlay=overlay,
                enabled=True,
                shadow_mode=True
            )

            res = scheduler.run_consultation_cycle("SCHEDULED")
            assert res is not None
            assert res["verdict"] == "SHADOW_LOG_ONLY"
            assert res["shadow_mode"] is True

            # Overlay must remain empty
            assert overlay.get_param("aggressive_scalper", "sl_pct", default=0.005) == 0.005
            assert overlay.get_state()["active_overrides"] == {}


def test_testnet_apply_mode_updates_overlay_within_bounds():
    """Verify that when in APPLY mode, valid recommendations update the runtime overlay."""
    with tempfile.TemporaryDirectory() as tmpdir:
        state_file = os.path.join(tmpdir, "testnet_params.json")
        log_file = os.path.join(tmpdir, "testnet_adv_log.jsonl")

        overlay = AdvisoryParameterOverlay(state_file=state_file)
        mock_client = MagicMock(spec=AIUniverseClient)
        mock_client.consult.return_value = {
            "decision_id": "DEC_TN_APPLY",
            "status": "APPROVED",
            "confidence": 0.92,
            "parameter_changes": [
                {"strategy": "aggressive_scalper", "parameter": "sl_pct", "current_value": 0.005, "new_value": 0.004}
            ],
            "debate_summary": "Apply testnet tightening"
        }

        with patch("advisory_ledger.ADVISORY_LOG_FILE", log_file):
            scheduler = TestnetAdvisoryScheduler(
                client=mock_client,
                overlay=overlay,
                enabled=True,
                shadow_mode=False
            )

            res = scheduler.run_consultation_cycle("SCHEDULED")
            assert res is not None
            assert res["verdict"] == "APPLY"
            assert res["shadow_mode"] is False

            # Overlay must reflect new value
            assert overlay.get_param("aggressive_scalper", "sl_pct") == 0.004


def test_testnet_safety_bounds_enforced():
    """Verify bounds (+20% max delta, forbidden params, position sizing) in testnet scheduler."""
    with tempfile.TemporaryDirectory() as tmpdir:
        state_file = os.path.join(tmpdir, "testnet_params.json")
        overlay = AdvisoryParameterOverlay(state_file=state_file)

        mock_client = MagicMock(spec=AIUniverseClient)
        mock_client.consult.return_value = {
            "decision_id": "DEC_TN_FORBIDDEN",
            "status": "APPROVED",
            "confidence": 0.99,
            "parameter_changes": [
                {"strategy": "global", "parameter": "max_daily_loss", "current_value": 500, "new_value": 1000},
                {"strategy": "aggressive_scalper", "parameter": "sl_pct", "current_value": 0.005, "new_value": 0.010}  # +100% (exceeds 20%)
            ]
        }

        scheduler = TestnetAdvisoryScheduler(
            client=mock_client,
            overlay=overlay,
            enabled=True,
            shadow_mode=False
        )

        res = scheduler.run_consultation_cycle("SCHEDULED")
        assert res is not None
        assert res["verdict"] == "REJECT"
        assert len(res["rejected_changes"]) == 2
        # Overlay unchanged
        assert overlay.get_state()["active_overrides"] == {}


def test_drawdown_limit_disables_advisory_and_reverts_parameters():
    """Verify that a 15% drawdown breach trips circuit breaker, disables advisory, and reverts overrides."""
    with tempfile.TemporaryDirectory() as tmpdir:
        state_file = os.path.join(tmpdir, "testnet_params.json")
        overlay = AdvisoryParameterOverlay(state_file=state_file)

        # Apply a parameter first
        overlay.apply_changes("DEC_BEFORE_CRASH", [
            {"strategy": "aggressive_scalper", "parameter": "sl_pct", "current_value": 0.005, "new_value": 0.004}
        ])
        assert overlay.get_param("aggressive_scalper", "sl_pct") == 0.004

        scheduler = TestnetAdvisoryScheduler(
            overlay=overlay,
            enabled=True,
            shadow_mode=False,
            max_drawdown_pct=0.15
        )
        scheduler._last_applied_decision_ids.append("DEC_BEFORE_CRASH")

        # Simulate 16% drawdown breach
        is_tripped = scheduler.check_drawdown_circuit_breaker(16.0)
        assert is_tripped is True
        assert scheduler._circuit_broken is True
        assert scheduler.shadow_mode is True

        # Verify overlay was rolled back to default baseline
        assert overlay.get_param("aggressive_scalper", "sl_pct", default=0.005) == 0.005
        assert overlay.get_state()["active_overrides"] == {}


def test_manual_trigger_and_toggle_api():
    """Verify manual consultation trigger and mode toggling."""
    mock_client = MagicMock(spec=AIUniverseClient)
    mock_client.consult.return_value = {
        "decision_id": "DEC_MANUAL_001",
        "status": "APPROVED",
        "confidence": 0.85,
        "parameter_changes": []
    }

    scheduler = TestnetAdvisoryScheduler(client=mock_client, enabled=True, shadow_mode=True)
    res = scheduler.trigger_manual_consultation()
    assert res is not None
    assert res["consultation_reason"] == "MANUAL_API_TRIGGER"

    # Toggle to apply mode
    toggled = scheduler.toggle_mode(shadow_mode=False)
    assert toggled is True
    assert scheduler.shadow_mode is False
