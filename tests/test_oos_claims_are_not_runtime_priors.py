"""No strategy may carry an out-of-sample claim its governance status does not support.

Historical incident this pins shut: PRODUCTION_STRATEGY_REGISTRY correctly marks
adx_ema_mtf DISABLED and nulls the registry copy of its OOS fields, but
strategy_adx_ema_mtf reads the *raw* ADX_EMA_MTF_STRATEGY dict instead. That dict
still held a 0.516 win-rate prior and 16 "validated" assets, so every signal the
module emitted carried an unverified confidence that governance had explicitly
withdrawn. Governance was correct; the bypass was the bug.

The rule, applied to every strategy config in the module:

    status != "VALIDATED"  =>  no prior, no validated-asset list, ever.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import config_strategy as cfg  # noqa: E402

#: The raw config dicts that any strategy module may read directly.
RAW_STRATEGY_CONFIGS = [
    ("ADX_EMA_STRATEGY", cfg.ADX_EMA_STRATEGY, "adx_ema"),
    ("ADX_EMA_STRATEGY_V2", cfg.ADX_EMA_STRATEGY_V2, "adx_ema"),
    ("ADX_EMA_MTF_STRATEGY", cfg.ADX_EMA_MTF_STRATEGY, "adx_ema_mtf"),
]

PARAM_IDS = [name for name, _, _ in RAW_STRATEGY_CONFIGS]


@pytest.mark.parametrize("name,raw,registry_key", RAW_STRATEGY_CONFIGS, ids=PARAM_IDS)
def test_unpromoted_strategies_carry_no_oos_prior(name, raw, registry_key):
    entry = cfg.PRODUCTION_STRATEGY_REGISTRY[registry_key]
    if entry.get("status") == "VALIDATED":
        pytest.skip(f"{registry_key} is promoted; its claims are its own evidence")
    assert raw.get("OOS_WIN_RATE_PRIOR") is None, (
        f"{registry_key} reads this dict directly, so a prior here reaches live signals "
        f"even though governance status is {entry.get('status')!r}"
    )


@pytest.mark.parametrize("name,raw,registry_key", RAW_STRATEGY_CONFIGS, ids=PARAM_IDS)
def test_unpromoted_strategies_claim_no_validated_assets(name, raw, registry_key):
    entry = cfg.PRODUCTION_STRATEGY_REGISTRY[registry_key]
    if entry.get("status") == "VALIDATED":
        pytest.skip(f"{registry_key} is promoted")
    assert raw.get("OOS_VALIDATED_ASSETS") == [], (
        f"{registry_key} lists validated assets its governance status does not support"
    )
    assert entry.get("validated_assets") == []


@pytest.mark.parametrize("name,raw,registry_key", RAW_STRATEGY_CONFIGS, ids=PARAM_IDS)
def test_validation_status_is_not_validated_while_unpromoted(name, raw, registry_key):
    entry = cfg.PRODUCTION_STRATEGY_REGISTRY[registry_key]
    if entry.get("status") == "VALIDATED":
        pytest.skip(f"{registry_key} is promoted")
    assert raw.get("OOS_VALIDATION_STATUS") != "VALIDATED"


def test_registry_null_out_block_covers_every_entry():
    """The block that withdraws registry priors must actually run for each entry."""
    for name, entry in cfg.PRODUCTION_STRATEGY_REGISTRY.items():
        if entry.get("status") != "VALIDATED":
            assert entry.get("oos_win_rate_prior") is None, f"{name} kept a registry prior"
            assert entry.get("validated_assets") == [], f"{name} kept registry assets"


def test_mtf_module_no_longer_bakes_in_a_default_prior():
    """The .get() default was the bypass: a missing key silently became 0.516."""
    import strategy_adx_ema_mtf as mtf

    assert mtf._OOS_WIN_RATE_PRIOR is None
    assert "OOS_WIN_RATE_PRIOR" in mtf._CFG
    assert mtf._CFG["OOS_WIN_RATE_PRIOR"] is None


def test_no_strategy_raises_its_persistence_gate_to_one():
    """The confidence=1.0 defect (commit 0d2bf30) must stay dead."""
    import strategy_adx_ema as adx
    import strategy_adx_ema_mtf as mtf

    for module in (adx, mtf):
        for value in (module._OOS_WIN_RATE_PRIOR,):
            assert value is None or value < 1.0
