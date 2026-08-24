import pytest
import pandas as pd
import numpy as np
from strategy_aggressive_scalper import get_signal, add_features, SignalResult


def _generate_synthetic_candles(n_bars=60, base_price=100.0, trend="BULLISH"):
    np.random.seed(42)
    timestamps = pd.date_range("2026-08-24 12:00:00", periods=n_bars, freq="1min")
    prices = [base_price]
    
    for i in range(1, n_bars):
        if trend == "BULLISH":
            delta = np.random.uniform(0.1, 0.5)
        elif trend == "BEARISH":
            delta = np.random.uniform(-0.5, -0.1)
        else:
            delta = np.random.uniform(-0.2, 0.2)
        prices.append(prices[-1] + delta)
        
    df = pd.DataFrame({
        "timestamp": timestamps,
        "open": prices,
        "high": [p + 0.3 for p in prices],
        "low": [p - 0.3 for p in prices],
        "close": [p + 0.1 for p in prices],
        "volume": [1000.0 + i * 10 for i in range(n_bars)]
    })
    return df


def test_aggressive_scalper_bullish_cross():
    df = _generate_synthetic_candles(50, trend="BULLISH")
    df = add_features(df)
    
    # Simulate fresh EMA9 crossing above EMA21 on last bar
    df.loc[df.index[-2], 'ema_9'] = 100.0
    df.loc[df.index[-2], 'ema_21'] = 100.5
    df.loc[df.index[-1], 'ema_9'] = 101.0
    df.loc[df.index[-1], 'ema_21'] = 100.5
    
    sig = get_signal(df)
    assert sig.side == "BUY"
    assert sig.sl < df['close'].iloc[-1]
    assert sig.tp > df['close'].iloc[-1]
    assert sig.rr_ratio == 2.0


def test_aggressive_scalper_bearish_cross():
    df = _generate_synthetic_candles(50, trend="BEARISH")
    df = add_features(df)
    
    # Simulate fresh EMA9 crossing below EMA21 on last bar
    df.loc[df.index[-2], 'ema_9'] = 100.5
    df.loc[df.index[-2], 'ema_21'] = 100.0
    df.loc[df.index[-1], 'ema_9'] = 99.5
    df.loc[df.index[-1], 'ema_21'] = 100.0
    
    sig = get_signal(df)
    assert sig.side == "SELL"
    assert sig.sl > df['close'].iloc[-1]
    assert sig.tp < df['close'].iloc[-1]
    assert sig.rr_ratio == 2.0


def test_aggressive_scalper_no_cross():
    df = _generate_synthetic_candles(50, trend="BULLISH")
    df = add_features(df)
    
    # EMA9 already above EMA21 on both bars
    df.loc[df.index[-2], 'ema_9'] = 101.0
    df.loc[df.index[-2], 'ema_21'] = 100.0
    df.loc[df.index[-1], 'ema_9'] = 102.0
    df.loc[df.index[-1], 'ema_21'] = 100.5
    
    sig = get_signal(df)
    assert sig.side is None
