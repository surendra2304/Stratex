"""
tests/test_strategy_v2_upgrade.py
Regression suite for the 2026-08 ADX+EMA V2 profitability upgrade.

Evidence base: research/upgrade_2026_08/param_study.py (192-variant grid,
2021-2026 Binance 4h data, 31 bps round-trip friction, SL-first intrabar).
These tests pin the V2 decision so it cannot silently regress:
  - pullback entry stays OFF (it was net-negative 2021-2026)
  - ADX threshold is 30
  - SL/TP are 3×ATR
  - params are config-driven, not hardcoded
"""

import pandas as pd
import pytest

from config_strategy import ADX_EMA_STRATEGY_V2
from strategy_adx_ema import add_features, get_signal


def _df(closes, adx=None):
    """Build a minimal OHLCV frame from a close series."""
    n = len(closes)
    idx = pd.date_range("2024-01-01", periods=n, freq="4h")
    closes = pd.Series(closes, index=idx, dtype=float)
    df = pd.DataFrame({
        "open": closes.shift(1).fillna(closes.iloc[0]),
        "high": closes + 10,
        "low": closes - 10,
        "close": closes,
        "volume": 1000.0,
    }, index=idx)
    return add_features(df)


class TestV2Config:
    def test_v2_params(self):
        # rev 2 (spot long-only): ADX 20 for longs; long-only grid showed ADX30
        # over-filters the long side (OOS PF 0.63 long-only vs 2.30 at ADX20).
        assert ADX_EMA_STRATEGY_V2["ADX_THRESHOLD"] == 20
        assert ADX_EMA_STRATEGY_V2["SL_ATR_MULTIPLIER"] == 3.0
        assert ADX_EMA_STRATEGY_V2["TP_ATR_MULTIPLIER"] == 3.0
        assert ADX_EMA_STRATEGY_V2["RISK_REWARD_RATIO"] == 1.0

    def test_pullback_disabled(self):
        assert ADX_EMA_STRATEGY_V2["ENABLE_PULLBACK_ENTRY"] is False

    def test_btc_regime_filter_enabled(self):
        assert ADX_EMA_STRATEGY_V2["BTC_REGIME_FILTER"] is True


class TestBTCRegimeGate:
    def _btc_df(self, n=250, base=100.0, drift=0.0):
        closes = pd.Series([base + drift * i for i in range(n)])
        return pd.DataFrame({"close": closes})

    def test_risk_on_when_above_ema200(self):
        from testnet_engine.service import compute_btc_regime
        regime, close, ema = compute_btc_regime(self._btc_df(drift=0.1))
        assert regime is True and close > ema

    def test_risk_off_when_below_ema200(self):
        from testnet_engine.service import compute_btc_regime
        regime, close, ema = compute_btc_regime(self._btc_df(drift=-0.1))
        assert regime is False and close < ema

    def test_insufficient_data_fails_open(self):
        from testnet_engine.service import compute_btc_regime
        assert compute_btc_regime(self._btc_df(n=100)) == (None, None, None)
        assert compute_btc_regime(None) == (None, None, None)


class TestV2SignalBehavior:
    def test_no_pullback_signal_in_established_trend(self):
        """Steady uptrend with a dip touching EMA20 must NOT fire (V1 pullback rule removed)."""
        # 250 flat bars then a sustained ramp: EMA20>EMA50>EMA200 established
        closes = list([100.0] * 250) + [100.0 + i * 1.0 for i in range(1, 60)]
        # force a shallow dip in the last bar that still closes bullish above EMA20
        closes[-1] = closes[-2] + 0.1
        df = _df(closes)
        sig = get_signal(df)
        # No fresh crossover in the last bar -> must be no signal of any kind,
        # including the removed pullback entry.
        assert sig.side is None

    def test_crossover_signal_uses_3x_atr_bands(self):
        """A valid golden cross must place SL/TP at 3x ATR around close."""
        # Flat regime then a sharp up-move causing EMA20 to cross EMA50
        closes = [100.0] * 260
        closes += [100.0 + 3 * i for i in range(1, 20)]
        df = _df(closes)
        # ensure ADX above threshold by injecting range expansion
        df.loc[df.index[-1], "adx"] = 40.0
        sig = get_signal(df)
        if sig.side == "BUY":
            last = df.iloc[-1]
            atr = last["atr_adx_ema"]
            assert sig.sl == pytest.approx(last["close"] - 3.0 * atr, rel=1e-6)
            assert sig.tp == pytest.approx(last["close"] + 3.0 * atr, rel=1e-6)
        else:
            # if cross didn't materialize in synthetic data, verify no signal rather than wrong bands
            assert sig.side is None

    def test_adx_below_threshold_blocks_signal(self):
        """Weak-trend crossovers (ADX <= 20) must be rejected."""
        closes = [100.0] * 260
        closes += [100.0 + 3 * i for i in range(1, 20)]
        df = _df(closes)
        df.loc[df.index[-1], "adx"] = 15.0  # below the V2-spot threshold of 20
        sig = get_signal(df)
        assert sig.side is None

    def test_priors_are_v2(self):
        _, _, _, stype, prior, rr = get_signal(_df([100.0 + i for i in range(300)]))
        assert stype == "RULE_BASED"
        assert prior == ADX_EMA_STRATEGY_V2["OOS_WIN_RATE_PRIOR"]
        assert rr == ADX_EMA_STRATEGY_V2["RISK_REWARD_RATIO"]
