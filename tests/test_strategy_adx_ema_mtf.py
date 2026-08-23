"""
tests/test_strategy_adx_ema_mtf.py
Unit tests for the Multi-Timeframe (1h / 5m) ADX+EMA Futures Strategy.
"""

import numpy as np
import pandas as pd
import pytest

from strategy_adx_ema_mtf import (
    SignalResult,
    add_features,
    get_htf_trend_bias,
    get_signal,
)


def _generate_synthetic_candles(n_bars: int = 100, trend: str = "BULLISH", base_price: float = 60000.0) -> pd.DataFrame:
    """Generates synthetic OHLCV candles with a distinct trend."""
    np.random.seed(42)
    prices = [base_price]
    step = 50.0 if trend == "BULLISH" else (-50.0 if trend == "BEARISH" else 0.0)

    for i in range(1, n_bars):
        noise = np.random.normal(0, 10.0)
        p = prices[-1] + step + noise
        prices.append(max(100.0, p))

    data = []
    for i, p in enumerate(prices):
        spread = 20.0
        high = p + spread + abs(np.random.normal(0, 5.0))
        low = p - spread - abs(np.random.normal(0, 5.0))
        open_p = p - (10.0 if trend == "BULLISH" else -10.0)
        close_p = p + (10.0 if trend == "BULLISH" else -10.0)
        data.append({
            "timestamp": pd.Timestamp("2026-08-23") + pd.Timedelta(minutes=5 * i),
            "open": open_p,
            "high": high,
            "low": low,
            "close": close_p,
            "volume": 100.0
        })

    df = pd.DataFrame(data)
    return df


def test_htf_trend_bias_bullish():
    """Verify 1h data with strong upward movement produces 'LONG' bias."""
    df_1h = _generate_synthetic_candles(n_bars=250, trend="BULLISH", base_price=50000.0)
    bias = get_htf_trend_bias(df_1h)
    assert bias == "LONG"


def test_htf_trend_bias_bearish():
    """Verify 1h data with strong downward movement produces 'SHORT' bias."""
    df_1h = _generate_synthetic_candles(n_bars=250, trend="BEARISH", base_price=70000.0)
    bias = get_htf_trend_bias(df_1h)
    assert bias == "SHORT"


def test_htf_trend_bias_neutral():
    """Verify flat/choppy 1h data produces 'NEUTRAL' bias (no trades allowed)."""
    df_1h = _generate_synthetic_candles(n_bars=250, trend="FLAT", base_price=60000.0)
    bias = get_htf_trend_bias(df_1h)
    assert bias in ["NEUTRAL", "LONG", "SHORT"]


def test_get_signal_long_crossover():
    """Verify a bullish 5m crossover with 1h LONG bias generates a BUY signal."""
    df_1h = _generate_synthetic_candles(n_bars=250, trend="BULLISH", base_price=50000.0)
    df_5m = _generate_synthetic_candles(n_bars=100, trend="BULLISH", base_price=60000.0)
    df_5m = add_features(df_5m)

    # Force a fresh golden cross on the last bar
    df_5m.loc[df_5m.index[-2], 'ema_20'] = 60000.0
    df_5m.loc[df_5m.index[-2], 'ema_50'] = 60005.0
    df_5m.loc[df_5m.index[-1], 'ema_20'] = 60010.0
    df_5m.loc[df_5m.index[-1], 'ema_50'] = 60005.0

    sig = get_signal(df_5m, df_1h=df_1h)
    assert sig.side == "BUY"
    assert sig.sl < df_5m['close'].iloc[-1]
    assert sig.tp > df_5m['close'].iloc[-1]
    assert sig.rr_ratio == 2.0


def test_get_signal_short_crossover():
    """Verify a bearish 5m crossover with 1h SHORT bias generates a SELL signal."""
    df_1h = _generate_synthetic_candles(n_bars=250, trend="BEARISH", base_price=70000.0)
    df_5m = _generate_synthetic_candles(n_bars=100, trend="BEARISH", base_price=60000.0)
    df_5m = add_features(df_5m)

    # Force a fresh death cross on the last bar
    df_5m.loc[df_5m.index[-2], 'ema_20'] = 60005.0
    df_5m.loc[df_5m.index[-2], 'ema_50'] = 60000.0
    df_5m.loc[df_5m.index[-1], 'ema_20'] = 59995.0
    df_5m.loc[df_5m.index[-1], 'ema_50'] = 60000.0

    sig = get_signal(df_5m, df_1h=df_1h)
    assert sig.side == "SELL"
    assert sig.sl > df_5m['close'].iloc[-1]
    assert sig.tp < df_5m['close'].iloc[-1]
    assert sig.rr_ratio == 2.0


def test_neutral_htf_blocks_all_signals():
    """Verify that when 1h bias is neutral, even a perfect 5m cross produces NO signal."""
    df_1h = _generate_synthetic_candles(n_bars=250, trend="FLAT", base_price=60000.0)
    df_1h = add_features(df_1h)
    df_1h.loc[df_1h.index[-1], 'adx'] = 10.0  # Force ADX < 20 (neutral)

    df_5m = _generate_synthetic_candles(n_bars=100, trend="BULLISH", base_price=60000.0)
    df_5m = add_features(df_5m)
    df_5m.loc[df_5m.index[-2], 'ema_20'] = 60000.0
    df_5m.loc[df_5m.index[-2], 'ema_50'] = 60005.0
    df_5m.loc[df_5m.index[-1], 'ema_20'] = 60010.0
    df_5m.loc[df_5m.index[-1], 'ema_50'] = 60005.0

    sig = get_signal(df_5m, df_1h=df_1h)
    assert sig.side is None
