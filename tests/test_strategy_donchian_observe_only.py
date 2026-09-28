"""Unit tests for the unregistered, paper-candidate Donchian strategy."""

import numpy as np
import pandas as pd
import pytest

import strategy_donchian_observe_only as candidate


def flat_candles(size=220):
    close = np.full(size, 100.0)
    return pd.DataFrame({
        "open": close.copy(),
        "high": close + 0.5,
        "low": close - 0.5,
        "close": close.copy(),
        "volume": np.ones(size),
    })


def test_warmup_returns_no_signal():
    result = candidate.get_signal(flat_candles(100))
    assert result.side is None
    assert result.reason == "INSUFFICIENT_HISTORY"
    assert result.confidence is None


def test_completed_candle_breakout_has_atr_stop_but_no_win_rate():
    bars = flat_candles()
    bars.loc[len(bars) - 1, ["open", "high", "low", "close"]] = [100.0, 104.0, 99.8, 103.0]
    featured = candidate.add_candidate_features(bars)

    signal = candidate.get_signal(featured)

    assert signal.side == "BUY"
    assert signal.reason == "DONCHIAN20_UP_BREAK"
    assert signal.stop is not None and signal.stop < 103.0
    assert signal.strategy_type == "OBSERVE_ONLY"
    assert signal.confidence is None


def test_channel_uses_only_previously_closed_candles():
    bars = flat_candles()
    featured = candidate.add_candidate_features(bars)
    changed_current_wick = bars.copy()
    changed_current_wick.loc[len(bars) - 1, "high"] = 10000.0
    featured_changed = candidate.add_candidate_features(changed_current_wick)

    assert featured.iloc[-1]["candidate_entry_high"] == featured_changed.iloc[-1]["candidate_entry_high"]
    assert featured.iloc[-1]["candidate_exit_low"] == featured_changed.iloc[-1]["candidate_exit_low"]


def test_opposite_channel_exit_is_directional():
    bars = flat_candles(220)
    bars.loc[len(bars) - 1, ["open", "high", "low", "close"]] = [98.5, 99.0, 97.5, 98.0]
    featured = candidate.add_candidate_features(bars)

    assert candidate.get_channel_exit(featured, "BUY") is True
    assert candidate.get_channel_exit(featured, "SELL") is False


def test_missing_or_invalid_ohlc_is_rejected():
    with pytest.raises(ValueError, match="missing required columns"):
        candidate.add_candidate_features(pd.DataFrame({"close": [1.0]}))
    bars = flat_candles(3)
    bars.loc[1, "close"] = np.nan
    with pytest.raises(ValueError, match="non-finite"):
        candidate.add_candidate_features(bars)


def test_invalid_exit_side_is_rejected():
    with pytest.raises(ValueError, match="side must be BUY or SELL"):
        candidate.get_channel_exit(flat_candles(220), "LONG")
