"""
tests/test_shadow_mode.py — Comprehensive tests verifying Shadow Mode integrity & double-key safety.

Verifications:
1. ADVISORY_SHADOW_MODE defaults to True in config.
2. When True, advisory_scheduler.run_consultation_cycle() NEVER calls advisory_params.apply_changes().
3. When True, all valid recommendations write to advisory_log.jsonl with verdict='SHADOW_LOG_ONLY'.
4. When True, advisory_params overlay remains empty.
5. If someone sets ADVISORY_SHADOW_MODE=False in environment, validate_config() raises a fatal ValueError
   unless ADVISORY_AUTONOMY_CONFIRMED=True is also provided (double-key safety).
"""

import os
import tempfile
from unittest.mock import MagicMock, patch
import pytest

import config
from advisory_gate import AdvisoryGate
from advisory_params import AdvisoryParameterOverlay
from advisory_scheduler import AdvisoryScheduler
from ai_universe_client import AIUniverseClient


def test_shadow_mode_defaults_to_true():
    """Verify ADVISORY_SHADOW_MODE defaults to True."""
    # Ensure config default is True
    assert config.ADVISORY_SHADOW_MODE is True


def test_shadow_mode_never_applies_changes_to_overlay():
    """Verify that when shadow_mode=True, overlay.apply_changes is never called and overlay remains empty."""
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as tf_state, \
         tempfile.NamedTemporaryFile(suffix=".jsonl", delete=False) as tf_log:
        temp_state_file = tf_state.name
        temp_log_file = tf_log.name

    try:
        mock_client = MagicMock(spec=AIUniverseClient)
        mock_client.consult.return_value = {
            "decision_id": "DEC_SHADOW_001",
            "status": "APPROVED",
            "confidence": 0.95,
            "parameter_changes": [
                {
                    "strategy": "aggressive_scalper",
                    "parameter": "sl_pct",
                    "current_value": 0.005,
                    "new_value": 0.004,
                    "reason": "Optimize risk"
                }
            ],
            "debate_summary": "High confidence optimization"
        }

        # Create overlay with custom temp state file
        overlay = AdvisoryParameterOverlay(state_file=temp_state_file)

        with patch("advisory_params.get_advisory_overlay", return_value=overlay), \
             patch("advisory_ledger.ADVISORY_LOG_FILE", temp_log_file):

            scheduler = AdvisoryScheduler(client=mock_client, shadow_mode=True)
            result = scheduler.run_consultation_cycle(reason="UNIT_TEST")

            # 1. Result verdict must be SHADOW_LOG_ONLY
            assert result is not None
            assert result["verdict"] == "SHADOW_LOG_ONLY"
            assert result["shadow_mode"] is True
            assert len(result["applied_changes"]) == 1

            # 2. Overlay must remain completely empty
            assert overlay.get_state()["active_overrides"] == {}
            assert overlay.get_param("aggressive_scalper", "sl_pct", default=0.005) == 0.005

            # 3. Log file must contain SHADOW_LOG_ONLY entry
            from advisory_ledger import read_recent_advisory_entries
            entries = read_recent_advisory_entries(limit=5, filepath=temp_log_file)
            assert len(entries) == 1
            assert entries[0]["decision_id"] == "DEC_SHADOW_001"
            assert entries[0]["verdict"] == "SHADOW_LOG_ONLY"
            assert entries[0]["shadow_mode"] is True

    finally:
        if os.path.exists(temp_state_file):
            os.remove(temp_state_file)
        if os.path.exists(temp_log_file):
            os.remove(temp_log_file)


def test_double_key_safety_refuses_start_without_confirmation():
    """Verify that setting ADVISORY_SHADOW_MODE=False without ADVISORY_AUTONOMY_CONFIRMED=True raises ValueError."""
    # Attempt validation with shadow_mode=False and autonomy_confirmed=False
    with patch.dict(os.environ, {"ADVISORY_SHADOW_MODE": "False", "ADVISORY_AUTONOMY_CONFIRMED": "False"}):
        with patch.object(config, "ADVISORY_SHADOW_MODE", False), \
             patch.object(config, "ADVISORY_AUTONOMY_CONFIRMED", False):
            with pytest.raises(ValueError) as exc_info:
                config.validate_config()
            assert "ADVISORY_AUTONOMY_CONFIRMED is not set to True" in str(exc_info.value)
            assert "double-key confirmation" in str(exc_info.value)


def test_double_key_safety_permits_start_when_both_keys_confirmed():
    """Verify that setting BOTH ADVISORY_SHADOW_MODE=False and ADVISORY_AUTONOMY_CONFIRMED=True passes validation."""
    with patch.dict(os.environ, {"ADVISORY_SHADOW_MODE": "False", "ADVISORY_AUTONOMY_CONFIRMED": "True"}):
        with patch.object(config, "ADVISORY_SHADOW_MODE", False), \
             patch.object(config, "ADVISORY_AUTONOMY_CONFIRMED", True):
            # Should not raise
            config.validate_config()
