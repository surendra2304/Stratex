import numpy as np
import pandas as pd
import pytest

from strategy_aggressive_scalper import add_features, get_signal


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


def test_aggressive_scalper_bullish_green_candle():
    df = _generate_synthetic_candles(50, trend="BULLISH")
    df = add_features(df)
    
    # Last candle is GREEN: Close > Open
    df.loc[df.index[-1], 'open'] = 100.0
    df.loc[df.index[-1], 'close'] = 101.0
    
    sig = get_signal(df)
    assert sig.side == "BUY"
    assert sig.sl < df['close'].iloc[-1]
    assert sig.tp > df['close'].iloc[-1]
    assert sig.rr_ratio == 0.6
    assert sig.sl == pytest.approx(101.0 * 0.995, rel=1e-4)
    assert sig.tp == pytest.approx(101.0 * 1.003, rel=1e-4)


def test_aggressive_scalper_bearish_red_candle():
    df = _generate_synthetic_candles(50, trend="BEARISH")
    df = add_features(df)
    
    # Last candle is RED: Close < Open
    df.loc[df.index[-1], 'open'] = 100.0
    df.loc[df.index[-1], 'close'] = 99.0
    
    sig = get_signal(df)
    assert sig.side == "SELL"
    assert sig.sl > df['close'].iloc[-1]
    assert sig.tp < df['close'].iloc[-1]
    assert sig.rr_ratio == 0.6
    assert sig.sl == pytest.approx(99.0 * 1.005, rel=1e-4)
    assert sig.tp == pytest.approx(99.0 * 0.997, rel=1e-4)


def test_aggressive_scalper_doji_no_signal():
    df = _generate_synthetic_candles(50, trend="BULLISH")
    df = add_features(df)
    
    # Last candle is DOJI: Close == Open
    df.loc[df.index[-1], 'open'] = 100.0
    df.loc[df.index[-1], 'close'] = 100.0
    
    sig = get_signal(df)
    assert sig.side is None
