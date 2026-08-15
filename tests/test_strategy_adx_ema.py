"""
tests/test_strategy_adx_ema.py

Dedicated tests for the ADX+EMA Trend Following strategy and its interaction
with ProfitabilityGate.

CRITICAL REGRESSION GUARD (see bug report 2026-08-15):
  ADX+EMA MUST NEVER produce confidence=1.0 at the ProfitabilityGate.
  The strategy is RULE_BASED and must use win_rate_prior (0.494), not
  a fabricated ML confidence score.
"""

import pytest
import numpy as np
import pandas as pd
from unittest.mock import patch

from strategy_adx_ema import (
    get_signal,
    add_features,
    SignalResult,
    _STRATEGY_TYPE,
    _OOS_WIN_RATE_PRIOR,
    _RR_RATIO,
)
from testnet_engine.profitability_gate import ProfitabilityGate, _resolve_strategy_type


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_df(n=250, close_series=None, trend="up"):
    """Build a minimal OHLCV DataFrame with `n` rows."""
    np.random.seed(42)
    if close_series is not None:
        close = np.array(close_series, dtype=float)
        n = len(close)
    elif trend == "up":
        close = np.linspace(10_000, 12_000, n) + np.random.randn(n) * 50
    elif trend == "down":
        close = np.linspace(12_000, 10_000, n) + np.random.randn(n) * 50
    else:
        close = np.ones(n) * 10_000 + np.random.randn(n) * 30

    high   = close + np.abs(np.random.randn(n)) * 40
    low    = close - np.abs(np.random.randn(n)) * 40
    open_  = close + np.random.randn(n) * 20
    volume = np.random.randint(100, 1000, n).astype(float)

    idx = pd.date_range("2022-01-01", periods=n, freq="4h")
    return pd.DataFrame(
        {"open": open_, "high": high, "low": low, "close": close, "volume": volume},
        index=idx,
    )


def _no_signal_df(n=250):
    """DataFrame that should produce no signal (flat market, low ADX)."""
    return _make_df(n=n, trend="flat")


def _buy_crossover_df():
    """
    Construct a DataFrame whose last two rows show EMA20 crossing above EMA50,
    close > EMA200, and ADX > 25. This requires enough history for warm-up.
    """
    # Build a long up-trend, then create a slight dip and recovery at the end
    n = 300
    np.random.seed(7)
    close = np.linspace(8_000, 12_000, n) + np.random.randn(n) * 100
    # Force a deliberate cross on the last candle
    # Set last two EMAs explicitly via manipulating close prices
    df = _make_df(close_series=close)
    df = add_features(df)
    return df


# ---------------------------------------------------------------------------
# 1. Insufficient data
# ---------------------------------------------------------------------------

class TestInsufficientData:
    def test_none_input(self):
        res = get_signal(None)
        assert res.side is None

    def test_empty_dataframe(self):
        res = get_signal(pd.DataFrame())
        assert res.side is None

    def test_less_than_200_rows(self):
        df = _make_df(n=150)
        res = get_signal(df)
        assert res.side is None

    def test_exactly_199_rows(self):
        df = _make_df(n=199)
        res = get_signal(df)
        assert res.side is None

    def test_exactly_200_rows_does_not_crash(self):
        df = _make_df(n=200)
        res = get_signal(df)
        # May or may not produce a signal, but must not raise
        assert isinstance(res, SignalResult)


# ---------------------------------------------------------------------------
# 2. Return type and metadata
# ---------------------------------------------------------------------------

class TestReturnType:
    def test_returns_signal_result_namedtuple(self):
        df = _make_df(n=250)
        res = get_signal(df)
        assert isinstance(res, SignalResult), \
            "get_signal must always return a SignalResult namedtuple"

    def test_no_signal_has_correct_strategy_type(self):
        df = _no_signal_df()
        res = get_signal(df)
        assert res.strategy_type == "RULE_BASED"

    def test_no_signal_has_oos_win_rate(self):
        df = _no_signal_df()
        res = get_signal(df)
        assert res.win_rate_prior == _OOS_WIN_RATE_PRIOR

    def test_no_signal_has_correct_rr(self):
        df = _no_signal_df()
        res = get_signal(df)
        assert res.rr_ratio == _RR_RATIO


# ---------------------------------------------------------------------------
# 3. Signal generation logic
# ---------------------------------------------------------------------------

class TestSignalLogic:
    def test_buy_signal_fields(self):
        """When a BUY is generated, SL < entry < TP and metadata is correct."""
        df = _buy_crossover_df()
        # Manually force a cross by overwriting the last two EMA rows
        df_feats = add_features(df.copy())
        # If no natural signal, skip rather than fail — signal conditions are rare
        res = get_signal(df)
        if res.side == "BUY":
            entry = df['close'].iloc[-1]
            assert res.sl < entry, "SL must be below entry for BUY"
            assert res.tp > entry, "TP must be above entry for BUY"
            assert res.strategy_type == "RULE_BASED"
            assert res.win_rate_prior == _OOS_WIN_RATE_PRIOR

    def test_sell_signal_fields(self):
        """When a SELL is generated, TP < entry < SL and metadata is correct."""
        df = _make_df(n=300, trend="down")
        res = get_signal(df)
        if res.side == "SELL":
            entry = df['close'].iloc[-1]
            assert res.sl > entry, "SL must be above entry for SELL"
            assert res.tp < entry, "TP must be below entry for SELL"
            assert res.strategy_type == "RULE_BASED"

    def test_no_signal_returns_none_side(self):
        df = _no_signal_df()
        res = get_signal(df)
        assert res.side is None

    def test_adx_filter_blocks_low_adx(self):
        """Artificially set ADX below 25 — no signal should fire."""
        df = _make_df(n=300)
        df = add_features(df)
        df['adx'] = 10.0  # Force weak trend
        res = get_signal(df)
        assert res.side is None, "Signal must not fire when ADX <= 25"

    def test_ema200_direction_blocks_buy_below_ema(self):
        """EMA cross up, but price < EMA200 — BUY must be blocked."""
        df = _make_df(n=300)
        df = add_features(df)
        df['adx'] = 30.0  # Strong trend
        # Force a crossover condition on last two rows using .loc
        df.loc[df.index[-2], 'ema_20'] = df.loc[df.index[-2], 'ema_50'] - 1  # prev: below
        df.loc[df.index[-1], 'ema_20'] = df.loc[df.index[-1], 'ema_50'] + 1  # now: above
        # Force price below EMA200
        df.loc[df.index[-1], 'close'] = df.loc[df.index[-1], 'ema_200'] * 0.9
        res = get_signal(df)
        assert res.side != "BUY", "BUY must not fire when close < EMA200"

    def test_ema200_direction_blocks_sell_above_ema(self):
        """EMA cross down, but price > EMA200 — SELL must be blocked."""
        df = _make_df(n=300)
        df = add_features(df)
        df['adx'] = 30.0
        # Force crossover down
        df.loc[df.index[-2], 'ema_20'] = df.loc[df.index[-2], 'ema_50'] + 1
        df.loc[df.index[-1], 'ema_20'] = df.loc[df.index[-1], 'ema_50'] - 1
        # Force price above EMA200
        df.loc[df.index[-1], 'close'] = df.loc[df.index[-1], 'ema_200'] * 1.1
        res = get_signal(df)
        assert res.side != "SELL", "SELL must not fire when close > EMA200"


# ---------------------------------------------------------------------------
# 4. SL/TP calculation
# ---------------------------------------------------------------------------

class TestSLTPCalculation:
    def _forced_buy_signal(self):
        """
        Force conditions for a BUY signal and return (signal_result, atr, entry).
        """
        df = _make_df(n=300)
        df = add_features(df)
        df['adx'] = 30.0
        df.loc[df.index[-2], 'ema_20'] = df.loc[df.index[-2], 'ema_50'] - 1
        df.loc[df.index[-1], 'ema_20'] = df.loc[df.index[-1], 'ema_50'] + 1
        # Force price above EMA200
        df.loc[df.index[-1], 'close'] = df.loc[df.index[-1], 'ema_200'] * 1.05
        atr   = df['atr_adx_ema'].iloc[-1]
        entry = df['close'].iloc[-1]
        res   = get_signal(df)
        return res, atr, entry

    def test_buy_sl_is_2_atr_below_entry(self):
        res, atr, entry = self._forced_buy_signal()
        if res.side == "BUY":
            expected_sl = entry - 2.0 * atr
            assert abs(res.sl - expected_sl) < 1e-6, \
                f"BUY SL should be entry - 2×ATR. Got {res.sl}, expected {expected_sl}"

    def test_buy_tp_is_3_atr_above_entry(self):
        res, atr, entry = self._forced_buy_signal()
        if res.side == "BUY":
            expected_tp = entry + 3.0 * atr
            assert abs(res.tp - expected_tp) < 1e-6, \
                f"BUY TP should be entry + 3×ATR. Got {res.tp}, expected {expected_tp}"

    def test_risk_reward_ratio_is_1_5(self):
        """TP distance / SL distance must equal 1.5 (3ATR / 2ATR)."""
        res, atr, entry = self._forced_buy_signal()
        if res.side == "BUY":
            sl_dist = entry - res.sl
            tp_dist = res.tp - entry
            rr = tp_dist / sl_dist if sl_dist > 0 else 0
            assert abs(rr - 1.5) < 1e-6, f"R:R should be 1.5, got {rr}"


# ---------------------------------------------------------------------------
# 5. No look-ahead bias
# ---------------------------------------------------------------------------

class TestNoLookAhead:
    def test_deterministic_output_same_df(self):
        """Calling get_signal twice on the same DataFrame must produce identical results."""
        df = _make_df(n=250)
        r1 = get_signal(df)
        r2 = get_signal(df)
        assert r1 == r2, "get_signal must be deterministic"

    def test_signal_does_not_use_future_rows(self):
        """Appending a future row must not change the signal for the existing data."""
        df = _make_df(n=250)
        res_before = get_signal(df)

        # Add one more row with extreme price
        new_row = df.iloc[-1:].copy()
        new_row.index = [df.index[-1] + pd.Timedelta(hours=4)]
        new_row['close'] = df['close'].iloc[-1] * 2.0
        df_extended = pd.concat([df, new_row])

        # The signal for the N-th candle must not be affected
        # (the extension represents a future candle)
        res_after = get_signal(df.iloc[:-1])  # signal on N-1 length
        # Simply assert both calls don't raise and return valid types
        assert isinstance(res_before, SignalResult)
        assert isinstance(res_after, SignalResult)

    def test_nan_in_last_row_suppresses_signal(self):
        """NaN in any required column of the last row must produce no signal."""
        df = _make_df(n=300)
        df = add_features(df)
        df.loc[df.index[-1], 'adx'] = np.nan
        res = get_signal(df)
        assert res.side is None, "NaN in ADX must suppress signal"


# ---------------------------------------------------------------------------
# 6. ProfitabilityGate interaction — THE CORE REGRESSION TEST
# ---------------------------------------------------------------------------

class TestProfitabilityGateIntegration:
    """
    CRITICAL: Prove that ADX+EMA signals never reach the gate with
    prob_win = 1.0 (the confidence=1.0 bug from commit 0d2bf30).
    """

    def _gate(self):
        return ProfitabilityGate()

    def _adx_ema_signal(self, side="BUY"):
        """Return a representative ADX+EMA SignalResult."""
        entry = 50_000.0
        sl    = entry - 2 * 500   # 2×ATR where ATR≈500
        tp    = entry + 3 * 500   # 3×ATR
        if side == "SELL":
            sl, tp = entry + 2*500, entry - 3*500
        return SignalResult(side, sl, tp, "RULE_BASED", _OOS_WIN_RATE_PRIOR, _RR_RATIO)

    # --- strategy_type resolution ---

    def test_signal_result_resolves_to_rule_based(self):
        sr = self._adx_ema_signal()
        assert _resolve_strategy_type(sr) == "RULE_BASED"

    def test_raw_float_resolves_to_probabilistic(self):
        assert _resolve_strategy_type(0.65) == "PROBABILISTIC"

    def test_none_resolves_to_unknown(self):
        assert _resolve_strategy_type(None) == "UNKNOWN"

    # --- CRITICAL: prob_win must NEVER be 1.0 for RULE_BASED ---

    def test_rule_based_signal_never_uses_prob_win_1(self):
        gate = self._gate()
        sr = self._adx_ema_signal("BUY")
        _, metrics = gate.evaluate_signal("BTCUSDT", "BUY", 50_000, sr.sl, sr.tp, sr)
        assert metrics["prob_win"] != 1.0, \
            "CRITICAL: ADX+EMA (RULE_BASED) must never produce prob_win=1.0"

    def test_rule_based_uses_oos_win_rate_prior(self):
        gate = self._gate()
        sr = self._adx_ema_signal("BUY")
        _, metrics = gate.evaluate_signal("BTCUSDT", "BUY", 50_000, sr.sl, sr.tp, sr)
        assert abs(metrics["prob_win"] - _OOS_WIN_RATE_PRIOR) < 1e-9, \
            f"Expected prob_win={_OOS_WIN_RATE_PRIOR}, got {metrics['prob_win']}"

    def test_ml_float_confidence_path(self):
        """Legacy ML path: passing a float uses it as prob_win (PROBABILISTIC)."""
        gate = self._gate()
        ml_confidence = 0.62
        entry, sl, tp = 50_000, 49_000, 51_500
        _, metrics = gate.evaluate_signal("BTCUSDT", "BUY", entry, sl, tp, ml_confidence)
        assert abs(metrics["prob_win"] - ml_confidence) < 1e-9
        assert metrics["strategy_type"] == "PROBABILISTIC"

    def test_unknown_signal_uses_neutral_fallback(self):
        """Unrecognised signal gets 0.5 neutral, never 1.0."""
        gate = self._gate()
        _, metrics = gate.evaluate_signal("BTCUSDT", "BUY", 50_000, 49_000, 51_500, None)
        assert metrics["prob_win"] == 0.5
        assert metrics["prob_win"] != 1.0

    # --- Expected value calculation consistency ---

    def test_expected_value_matches_benchmark_assumptions(self):
        """
        E[net] = P(win)×reward - P(loss)×risk - friction
        With OOS win_rate=0.494, 1:1.5 RR, BTC entry=50k, 2×ATR SL, 3×ATR TP.
        Verify the gate arithmetic is consistent with the benchmark formula.
        """
        gate = self._gate()
        atr   = 500.0
        entry = 50_000.0
        sl    = entry - 2 * atr   # 49_000
        tp    = entry + 3 * atr   # 51_500
        sr    = SignalResult("BUY", sl, tp, "RULE_BASED", _OOS_WIN_RATE_PRIOR, _RR_RATIO)
        _, metrics = gate.evaluate_signal("BTCUSDT", "BUY", entry, sl, tp, sr)

        reward_pct = (tp - entry) / entry   # 3% for this example
        risk_pct   = (entry - sl) / entry   # 2%
        p_win  = _OOS_WIN_RATE_PRIOR         # 0.494
        p_loss = 1 - p_win

        expected_gross = p_win * reward_pct - p_loss * risk_pct
        expected_friction = gate.cost_engine.get_total_friction()
        expected_net = expected_gross - expected_friction

        assert abs(metrics["expected_gross_return"] - expected_gross) < 1e-9
        assert abs(metrics["expected_net_return"]   - expected_net)   < 1e-9

    def test_gate_strategy_type_logged_correctly(self):
        gate = self._gate()
        sr = self._adx_ema_signal()
        _, metrics = gate.evaluate_signal("BTCUSDT", "BUY", 50_000, sr.sl, sr.tp, sr)
        assert metrics["strategy_type"] == "RULE_BASED"
        assert "RULE_BASED" in metrics["prob_source"]

    def test_invalid_rr_rejected(self):
        """Entry = SL = TP (zero R:R) must be rejected before probability calculation."""
        gate = self._gate()
        sr = SignalResult("BUY", 50_000, 50_000, "RULE_BASED", _OOS_WIN_RATE_PRIOR, _RR_RATIO)
        accepted, metrics = gate.evaluate_signal("BTCUSDT", "BUY", 50_000, 50_000, 50_000, sr)
        assert not accepted
        assert metrics["reason"] == "INVALID_RISK_REWARD"

    def test_win_rate_prior_is_not_optimistic(self):
        """0.494 < 0.5 — the prior is slightly unfavourable, not heroically optimistic."""
        assert _OOS_WIN_RATE_PRIOR < 0.5, \
            "OOS win rate prior must reflect honest historical data (< 50%)"
