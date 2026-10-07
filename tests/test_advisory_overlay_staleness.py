"""
Staleness TTL tests for the AI-Universe advisory parameter overlay.

Contract under test:
- AI-suggested parameter overrides stop steering trades once they are older
  than STRATEX_ADVISORY_MAX_AGE_HOURS (default 72h); the overlay degrades to
  strategy defaults instead of serving stale advice forever.
- Overrides without a provenance timestamp are treated as expired (fail-safe).
- Fresh applications reset the staleness clock.
- TTL enforcement can be disabled with a non-positive max age.
"""
import datetime
import json

import pytest

from advisory_params import AdvisoryParameterOverlay


@pytest.fixture
def overlay(tmp_path, monkeypatch):
    monkeypatch.delenv("STRATEX_ADVISORY_MAX_AGE_HOURS", raising=False)
    return AdvisoryParameterOverlay(state_file=str(tmp_path / "advisory_state.json"))


def _apply(overlay: AdvisoryParameterOverlay, value=0.02):
    assert overlay.apply_changes(
        "DEC-1",
        [{"strategy": "adx_ema", "parameter": "entry_atr_mult", "new_value": value, "current_value": 1.5}],
    )


def test_fresh_overlay_is_served(overlay):
    _apply(overlay, 0.02)
    assert overlay.get_param("adx_ema", "entry_atr_mult", default=1.5) == 0.02
    state = overlay.get_state()
    assert state["overlay_status"] == "ACTIVE"
    assert state["overrides_active"] is True


def test_stale_overlay_falls_back_to_defaults(overlay):
    _apply(overlay, 0.02)
    # Age the overlay past the default 72h budget.
    overlay._last_applied_time = datetime.datetime.utcnow() - datetime.timedelta(hours=73)
    assert overlay.get_param("adx_ema", "entry_atr_mult", default=1.5) == 1.5
    params = overlay.get_current_params("adx_ema")
    assert params.get("entry_atr_mult") != 0.02
    state = overlay.get_state()
    assert state["overlay_status"] == "STALE_IGNORED"
    assert state["overrides_active"] is False


def test_overlay_without_provenance_timestamp_is_expired(tmp_path, monkeypatch):
    monkeypatch.delenv("STRATEX_ADVISORY_MAX_AGE_HOURS", raising=False)
    state_file = tmp_path / "legacy_state.json"
    state_file.write_text(json.dumps({
        "last_applied_timestamp": None,
        "overrides": {"adx_ema": {"entry_atr_mult": 0.02}},
        "history": [],
        "pending_recommendations": {},
    }))
    overlay = AdvisoryParameterOverlay(state_file=str(state_file))
    assert overlay.get_param("adx_ema", "entry_atr_mult", default=1.5) == 1.5
    assert overlay.get_state()["overlay_status"] == "STALE_IGNORED"


def test_reapplication_resets_staleness(overlay):
    _apply(overlay, 0.02)
    overlay._last_applied_time = datetime.datetime.utcnow() - datetime.timedelta(hours=100)
    assert overlay.get_param("adx_ema", "entry_atr_mult", default=1.5) == 1.5
    _apply(overlay, 0.03)
    assert overlay.get_param("adx_ema", "entry_atr_mult", default=1.5) == 0.03
    assert overlay.get_state()["overlay_status"] == "ACTIVE"


def test_ttl_disabled_keeps_overrides(tmp_path, monkeypatch):
    monkeypatch.setenv("STRATEX_ADVISORY_MAX_AGE_HOURS", "0")
    overlay = AdvisoryParameterOverlay(state_file=str(tmp_path / "s.json"))
    _apply(overlay, 0.02)
    overlay._last_applied_time = datetime.datetime.utcnow() - datetime.timedelta(days=400)
    assert overlay.get_param("adx_ema", "entry_atr_mult", default=1.5) == 0.02
    assert overlay.get_state()["overlay_status"] == "ACTIVE_TTL_DISABLED"


def test_custom_ttl_window(tmp_path, monkeypatch):
    monkeypatch.setenv("STRATEX_ADVISORY_MAX_AGE_HOURS", "6")
    overlay = AdvisoryParameterOverlay(state_file=str(tmp_path / "s.json"))
    _apply(overlay, 0.02)
    overlay._last_applied_time = datetime.datetime.utcnow() - datetime.timedelta(hours=7)
    assert overlay.get_param("adx_ema", "entry_atr_mult", default=1.5) == 1.5


def test_invalid_ttl_env_falls_back_to_default(tmp_path, monkeypatch):
    monkeypatch.setenv("STRATEX_ADVISORY_MAX_AGE_HOURS", "garbage")
    overlay = AdvisoryParameterOverlay(state_file=str(tmp_path / "s.json"))
    assert overlay._max_age_hours == 72.0


def test_staleness_survives_restart(tmp_path, monkeypatch):
    """A stale overlay persisted to disk must still be ignored after reload."""
    monkeypatch.delenv("STRATEX_ADVISORY_MAX_AGE_HOURS", raising=False)
    path = tmp_path / "persist.json"
    first = AdvisoryParameterOverlay(state_file=str(path))
    _apply(first, 0.02)
    first._last_applied_time = datetime.datetime.utcnow() - datetime.timedelta(hours=200)
    first._save_state()

    reloaded = AdvisoryParameterOverlay(state_file=str(path))
    assert reloaded.get_param("adx_ema", "entry_atr_mult", default=1.5) == 1.5
    assert reloaded.get_state()["overlay_status"] == "STALE_IGNORED"
