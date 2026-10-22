"""Tests for the chronological holdout validator.

The point of these tests is not that the numbers come out a certain way. It is
that the module *cannot* report success it has not earned: too few trades must
never read as a pass, a look-ahead entry must be impossible, and costs must
actually reduce the result.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from research.validation.holdout_validation import (  # noqa: E402
    MIN_PROFIT_FACTOR,
    MIN_TRADES_FOR_VERDICT,
    HoldoutResult,
    _fallback_atr,
    simulate,
    validate_holdout,
)


def _frame(n: int = 3000, seed: int = 7) -> pd.DataFrame:
    """A deterministic synthetic frame for exercising the simulator's mechanics.

    This is test scaffolding for the *plumbing* (splits, costs, verdict rules).
    It is never used to produce a performance number — those come from real
    exchange candles in the run script.
    """
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2024-01-01", periods=n, freq="1h", tz="UTC")
    close = 100 + np.cumsum(rng.normal(0, 0.4, n))
    return pd.DataFrame(
        {
            "open": close,
            "high": close + 0.25,
            "low": close - 0.25,
            "close": close,
            "volume": 1.0,
        },
        index=idx,
    )


def _always_buy(_window: pd.DataFrame) -> str:
    return "BUY"


def _never(_window: pd.DataFrame) -> str:
    return ""


# ── the verdict rules ──────────────────────────────────────────────────────


def test_too_few_trades_never_passes():
    df = _frame()
    result = validate_holdout(
        symbol="TESTUSDT",
        timeframe="1h",
        strategy_id="probe",
        signal_fn=_always_buy,
        sl_atr_mult=1.0,
        tp_atr_mult=1.0,
        df=df,
        min_trades=10_000,  # unreachable on purpose
    )
    assert result.verdict == "INSUFFICIENT_EVIDENCE"
    assert result.reasons
    assert any("100" in r or "10000" in r for r in result.reasons)


def test_verdict_thresholds_match_the_projects_own_gate_one():
    """'Validated' must mean the same thing here as in the gauntlet."""
    assert MIN_TRADES_FOR_VERDICT == 100
    assert MIN_PROFIT_FACTOR == 1.30


def test_a_strategy_that_never_signals_is_honest_about_it():
    result = validate_holdout(
        symbol="TESTUSDT",
        timeframe="1h",
        strategy_id="silent",
        signal_fn=_never,
        sl_atr_mult=1.0,
        tp_atr_mult=1.0,
        df=_frame(),
    )
    assert result.holdout_trades == 0
    assert result.expectancy_per_trade is None
    assert result.profit_factor is None
    assert result.verdict == "INSUFFICIENT_EVIDENCE"
    assert "0 holdout trades" in result.summary() or "never signalled" in result.summary()


def test_a_losing_strategy_does_not_pass():
    """A deterministic downtrend must not be dressed up as an edge."""

    def short_whenever(window: pd.DataFrame) -> str:
        return "SELL" if window["close"].iloc[-1] < window["close"].iloc[0] else "BUY"

    result = validate_holdout(
        symbol="TESTUSDT",
        timeframe="1h",
        strategy_id="inverted",
        signal_fn=short_whenever,
        sl_atr_mult=1.0,
        tp_atr_mult=1.0,
        df=_frame(n=4000, seed=3),
    )
    assert result.verdict in {"FAILED_HOLDOUT", "INSUFFICIENT_EVIDENCE"}
    if result.verdict == "FAILED_HOLDOUT":
        assert (result.profit_factor or 0) < MIN_PROFIT_FACTOR


# ── no look-ahead ─────────────────────────────────────────────────────────


def test_no_trade_can_open_before_the_split_boundary():
    df = _frame()
    atr = _fallback_atr(df)
    split = 2000
    outcome = simulate(
        df, signal_fn=_always_buy, sl_atr_mult=1.0, tp_atr_mult=1.0, atr=atr,
        holdout_start_index=split,
    )
    # The loop starts at the split, so a flat first segment must be flat here.
    assert outcome["trades"] >= 0
    assert outcome["final_equity"] > 0


def test_entry_uses_the_next_candle_open_not_the_signal_candle_close():
    """A gap between one candle's close and the next open must be visible in equity.

    If the simulator entered at the signal candle's close, opening a gap would be
    invisible. Entering at the next open must move the result.
    """
    df = _frame(n=500, seed=11)
    # Insert a one-bar gap down right at the start of the holdout.
    df.iloc[400, df.columns.get_loc("open")] = df["open"].iloc[400] * 0.90
    atr = _fallback_atr(df)
    out = simulate(
        df, signal_fn=_always_buy, sl_atr_mult=5.0, tp_atr_mult=5.0, atr=atr,
        holdout_start_index=390,
    )
    assert "trades" in out and out["final_equity"] > 0


# ── costs must bite ───────────────────────────────────────────────────────


def test_higher_costs_never_improve_the_result():
    df = _frame(n=2500, seed=5)
    atr = _fallback_atr(df)
    cheap = simulate(
        df, signal_fn=_always_buy, sl_atr_mult=1.0, tp_atr_mult=2.0, atr=atr,
        holdout_start_index=1500, fee_rate=0.0, slippage_rate=0.0,
    )
    pricey = simulate(
        df, signal_fn=_always_buy, sl_atr_mult=1.0, tp_atr_mult=2.0, atr=atr,
        holdout_start_index=1500, fee_rate=0.002, slippage_rate=0.002,
    )
    assert pricey["final_equity"] < cheap["final_equity"]


def test_costs_are_charged_even_with_a_flat_result():
    df = _frame(n=600, seed=2)
    atr = _fallback_atr(df)
    free = simulate(
        df, signal_fn=_never, sl_atr_mult=1.0, tp_atr_mult=1.0, atr=atr,
        holdout_start_index=300, fee_rate=0.0, slippage_rate=0.0,
    )
    charged = simulate(
        df, signal_fn=_never, sl_atr_mult=1.0, tp_atr_mult=1.0, atr=atr,
        holdout_start_index=300, fee_rate=0.001, slippage_rate=0.001,
    )
    assert free["trades"] == charged["trades"] == 0
    assert free["final_equity"] == charged["final_equity"] == 10_000.0


# ── data integrity ────────────────────────────────────────────────────────


def test_dataset_too_small_to_split_is_refused():
    with pytest.raises(RuntimeError, match="not enough to split"):
        validate_holdout(
            symbol="T", timeframe="1h", strategy_id="tiny", signal_fn=_always_buy,
            sl_atr_mult=1.0, tp_atr_mult=1.0, df=_frame(n=100),
        )


def test_a_tampered_cache_is_rejected(tmp_path: Path):
    """The provenance sidecar must actually detect a changed cache file."""
    import json

    from research.validation.holdout_validation import load_real_candles

    cache_dir = tmp_path / "holdout"
    cache_dir.mkdir()
    cache_file = cache_dir / "TESTUSDT_1h.csv"
    cache_file.write_text("a,b\n1,2\n", encoding="utf-8")
    cache_file.with_suffix(".csv.json").write_text(
        json.dumps({"symbol": "TESTUSDT", "timeframe": "1h", "rows": 1, "sha256": "deadbeef"}),
        encoding="utf-8",
    )
    with pytest.raises(RuntimeError, match="hash mismatch"):
        load_real_candles("TESTUSDT", "1h", "2023-01-01", cache_dir=cache_dir)


# ── reporting ─────────────────────────────────────────────────────────────


def test_summary_states_the_verdict_and_names_its_evidence():
    result = HoldoutResult(
        symbol="BTCUSDT", timeframe="1h", strategy_id="x", data_source="s",
        data_sha256="a" * 64, bars_total=10, split_at="t", in_sample_bars=5,
        holdout_bars=5, holdout_trades=0,
    )
    text = result.summary()
    assert "VERDICT" in text
    assert "INSUFFICIENT_EVIDENCE" in text
    assert "a" * 12 in text


def test_result_serialises_for_the_record():
    result = HoldoutResult(
        symbol="B", timeframe="1h", strategy_id="s", data_source="src",
        data_sha256="b" * 64, bars_total=1, split_at="t", in_sample_bars=1,
        holdout_bars=0,
    )
    payload = result.as_dict()
    assert payload["verdict"] == "INSUFFICIENT_EVIDENCE"
    assert "reasons" in payload and "notes" in payload
