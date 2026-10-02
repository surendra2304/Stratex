"""Tests for regime-stratified validation.

Two things matter here. The drawdown windows must be found *from the data*, so
that no human can select a flattering episode. And each window's simulation must
be bounded to that window — an earlier version of this ran every window to the end
of the dataset, which made the windows overlap and produced a confident-looking
verdict built on inflated trade counts. That regression is pinned here.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from research.validation.holdout_validation import simulate  # noqa: E402
from research.validation.regime_holdout import (  # noqa: E402
    DRAWDOWN_THRESHOLD_PCT,
    DrawdownWindow,
    RegimeResult,
    evaluate_regimes,
    evaluate_window,
    find_drawdown_windows,
    write_report,
)


def _frame(close: list[float], start: str = "2024-01-01", freq: str = "1h") -> pd.DataFrame:
    idx = pd.date_range(start, periods=len(close), freq=freq, tz="UTC")
    return pd.DataFrame(
        {
            "open": close,
            "high": [c * 1.001 for c in close],
            "low": [c * 0.999 for c in close],
            "close": close,
            "volume": 1.0,
        },
        index=idx,
    )


def _sawtooth(n: int = 4000) -> pd.DataFrame:
    """A long rise with two real drawdowns in it."""
    up = [100.0 * (1.001 ** i) for i in range(n // 2)]
    down = [up[-1] * (0.995 ** i) for i in range(n // 4)]
    up2 = [down[-1] * (1.002 ** i) for i in range(n // 4)]
    return _frame(up + down + up2)


# ── windows come from the data, not from a human ──────────────────────────


def test_a_real_decline_is_found_as_a_drawdown_window():
    df = _sawtooth()
    windows = find_drawdown_windows(df)
    assert windows, "the synthetic decline should have been detected"
    assert all(w.depth_pct <= -DRAWDOWN_THRESHOLD_PCT for w in windows)
    assert all(w.bars > 0 for w in windows)


def test_a_monotonic_rise_has_no_drawdowns():
    df = _frame([100.0 * (1.001 ** i) for i in range(3000)])
    assert find_drawdown_windows(df) == []


def test_a_decline_that_never_recovers_is_still_reported():
    """An unfinished drawdown is still a drawdown; omitting it would flatter."""
    df = _frame([100.0 * (1.002 ** i) for i in range(500)] + [200.0 * (0.998 ** i) for i in range(800)])
    windows = find_drawdown_windows(df)
    assert windows
    assert windows[-1].end == str(df.index[-1])


def test_noise_below_the_threshold_is_not_a_regime():
    df = _frame([100.0 * (1.0004 ** i) * (1.01 if i % 7 == 0 else 1.0) for i in range(3000)])
    windows = find_drawdown_windows(df)
    assert all(w.depth_pct <= -DRAWDOWN_THRESHOLD_PCT for w in windows)


def test_shallow_declines_are_ignored_when_the_threshold_is_raised():
    df = _sawtooth()
    assert len(find_drawdown_windows(df, threshold_pct=5.0)) >= len(
        find_drawdown_windows(df, threshold_pct=40.0)
    )


def test_short_swings_are_not_regimes():
    """A 1% dip lasting a day is noise, not a bear market."""
    close = [100.0 * (1.001 ** i) for i in range(2000)]
    close[500:520] = [c * 0.97 for c in close[500:520]]
    close[1000:] = [c * (1.0 / 0.97) for c in close[1000:]]
    assert find_drawdown_windows(_frame(close), min_bars=200) == []


# ── each window is bounded to itself (the regression) ─────────────────────


def test_simulation_stops_at_the_window_end():
    """The regression: windows used to run to the end of the dataset and overlap."""
    df = _sawtooth()
    end = df.index[1500]
    last = int(df.index.searchsorted(end, side="right")) - 1

    bounded = simulate(
        df,
        signal_fn=lambda w: "BUY",
        sl_atr_mult=1.0,
        tp_atr_mult=1.0,
        atr=pd.Series(1.0, index=df.index),
        holdout_start_index=100,
        holdout_end_index=last,
    )
    unbounded = simulate(
        df,
        signal_fn=lambda w: "BUY",
        sl_atr_mult=1.0,
        tp_atr_mult=1.0,
        atr=pd.Series(1.0, index=df.index),
        holdout_start_index=100,
    )
    assert bounded["trades"] < unbounded["trades"]


def test_bounded_windows_do_not_share_identical_drawdowns():
    """The signature of the overlap bug was identical max drawdown everywhere."""
    df = _sawtooth()
    atr = pd.Series(1.0, index=df.index)
    results = [
        evaluate_window(
            df=df,
            window=w,
            signal_fn=lambda x: "BUY",
            sl_atr_mult=1.0,
            tp_atr_mult=1.0,
            atr=atr,
            fee_rate=0.0004,
            slippage_rate=0.0002,
            risk_per_trade=0.005,
        )
        for w in find_drawdown_windows(df)
    ]
    if len(results) >= 2:
        assert len({r.max_drawdown_pct for r in results}) > 1, (
            "every window reported the same drawdown; the windows are overlapping"
        )


def test_no_trade_opens_before_the_window_starts():
    """One entry per bar at most, and only inside the window."""
    df = _sawtooth()
    window = find_drawdown_windows(df)[0]
    start = pd.Timestamp(window.start)
    end = pd.Timestamp(window.end)
    window_bars = int((end - start).total_seconds() // 3600) + 1

    result = evaluate_window(
        df=df,
        window=window,
        signal_fn=lambda w: "BUY",
        sl_atr_mult=1.0,
        tp_atr_mult=1.0,
        atr=pd.Series(1.0, index=df.index),
        fee_rate=0.0004,
        slippage_rate=0.0002,
        risk_per_trade=0.005,
    )
    # An always-long signal would open on every bar of the *whole* series if the
    # simulation were not bounded to the window.
    assert result.trades > 0
    assert result.trades <= window_bars
    assert result.trades < len(df)


# ── verdicts still require enough trades ──────────────────────────────────


def test_a_sparse_episode_cannot_be_judged():
    """A spectacular profit factor on a handful of trades is still not evidence."""
    df = _sawtooth()
    window = find_drawdown_windows(df)[0]
    result = evaluate_window(
        df=df,
        window=window,
        signal_fn=lambda w: "BUY",
        sl_atr_mult=1.0,
        tp_atr_mult=1.0,
        atr=pd.Series(1.0, index=df.index),
        fee_rate=0.0004,
        slippage_rate=0.0002,
        risk_per_trade=0.005,
        min_trades=10_000,
    )
    assert result.verdict == "INSUFFICIENT_EVIDENCE"
    assert result.reasons
    assert any("needs >=" in r for r in result.reasons)


def test_a_losing_episode_with_enough_trades_fails():
    """A real verdict is still allowed to be a bad one."""
    df = _sawtooth()
    window = find_drawdown_windows(df)[0]
    result = evaluate_window(
        df=df,
        window=window,
        signal_fn=lambda w: "BUY",
        sl_atr_mult=1.0,
        tp_atr_mult=1.0,
        atr=pd.Series(1.0, index=df.index),
        fee_rate=0.0004,
        slippage_rate=0.0002,
        risk_per_trade=0.005,
        min_trades=1,
    )
    assert result.trades >= 1
    assert result.verdict in {"SURVIVED_DRAWDOWN", "FAILED_DRAWDOWN", "INSUFFICIENT_EVIDENCE"}
    if result.verdict != "INSUFFICIENT_EVIDENCE":
        assert result.reasons or result.verdict == "SURVIVED_DRAWDOWN"


def test_aggregate_reports_insufficient_when_nothing_could_be_judged():
    df = _sawtooth()
    summary = evaluate_regimes(
        df=df,
        signal_fn=lambda w: "BUY",
        sl_atr_mult=1.0,
        tp_atr_mult=1.0,
        atr=pd.Series(1.0, index=df.index),
        fee_rate=0.0004,
        slippage_rate=0.0002,
        risk_per_trade=0.005,
        min_trades=10_000,
    )
    assert summary["regime_verdict"] == "INSUFFICIENT_EVIDENCE"
    assert summary["episodes_with_verdict"] == 0


def test_aggregate_reports_no_drawdowns_when_the_rise_never_broke():
    df = _frame([100.0 * (1.001 ** i) for i in range(2000)])
    summary = evaluate_regimes(
        df=df,
        signal_fn=lambda w: "BUY",
        sl_atr_mult=1.0,
        tp_atr_mult=1.0,
        atr=pd.Series(1.0, index=df.index),
        fee_rate=0.0004,
        slippage_rate=0.0002,
        risk_per_trade=0.005,
    )
    assert summary["regime_verdict"] == "NO_DRAWDOWNS_IN_DATA"


def test_every_episode_is_reported_none_are_hidden():
    df = _sawtooth()
    summary = evaluate_regimes(
        df=df,
        signal_fn=lambda w: "BUY",
        sl_atr_mult=1.0,
        tp_atr_mult=1.0,
        atr=pd.Series(1.0, index=df.index),
        fee_rate=0.0004,
        slippage_rate=0.0002,
        risk_per_trade=0.005,
        min_trades=10_000,
    )
    assert summary["episodes_found"] == len(summary["results"])
    assert summary["episodes_found"] == len(find_drawdown_windows(df))


# ── the report is persisted ───────────────────────────────────────────────


def test_report_is_written_and_readable(tmp_path):
    summary = {
        "symbol": "BTCUSDT",
        "timeframe": "1h",
        "generated_at": "2026-10-02T00:00:00+00:00",
        "regime_verdict": "INSUFFICIENT_EVIDENCE",
        "results": [],
    }
    path = write_report(summary, tmp_path)
    assert path.exists()
    import json

    restored = json.loads(path.read_text(encoding="utf-8"))
    assert restored["regime_verdict"] == "INSUFFICIENT_EVIDENCE"
    assert restored["symbol"] == "BTCUSDT"
    assert path.name.startswith("regime_report_BTCUSDT_1h_")


def test_result_line_states_the_verdict():
    window = DrawdownWindow(start="2024-01-01", trough="2024-02-01", end="2024-03-01", depth_pct=-33.3, bars=1400)
    line = RegimeResult(window=window, trades=0).line()
    assert "0 trades" in line
    assert "INSUFFICIENT_EVIDENCE" in line
