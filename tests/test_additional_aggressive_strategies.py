import numpy as np
import pandas as pd

import strategy_bb_reversion
import strategy_rsi_burst
import strategy_vwap_trend


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
        "high": [p + 0.5 for p in prices],
        "low": [p - 0.5 for p in prices],
        "close": [p + 0.1 for p in prices],
        "volume": [1000.0 + i * 10 for i in range(n_bars)]
    })
    return df


def test_bb_reversion_long_signal():
    df = _generate_synthetic_candles(50, trend="BEARISH")
    df = strategy_bb_reversion.add_features(df)
    
    # Simulate previous bar piercing lower band, current bar closing back inside
    df.loc[df.index[-2], 'low'] = 80.0
    df.loc[df.index[-2], 'close'] = 82.0
    df.loc[df.index[-2], 'bb_lower'] = 85.0
    
    df.loc[df.index[-1], 'close'] = 87.0
    df.loc[df.index[-1], 'bb_lower'] = 85.0
    
    sig = strategy_bb_reversion.get_signal(df)
    assert sig.side == "BUY"
    assert sig.sl < df['close'].iloc[-1]
    assert sig.tp > df['close'].iloc[-1]
    assert sig.rr_ratio == 3.0


def test_rsi_burst_long_signal():
    df = _generate_synthetic_candles(50, trend="BEARISH")
    df = strategy_rsi_burst.add_features(df)
    
    # Simulate RSI crossing back above 30
    df.loc[df.index[-2], 'rsi_14'] = 25.0
    df.loc[df.index[-1], 'rsi_14'] = 32.0
    
    sig = strategy_rsi_burst.get_signal(df)
    assert sig.side == "BUY"
    assert sig.sl < df['close'].iloc[-1]
    assert sig.tp > df['close'].iloc[-1]
    assert sig.rr_ratio == 3.0


def test_vwap_trend_long_signal():
    df = _generate_synthetic_candles(50, trend="BULLISH")
    df = strategy_vwap_trend.add_features(df)
    
    # Simulate cross above VWAP with EMA9 > EMA21
    df.loc[df.index[-2], 'close'] = 98.0
    df.loc[df.index[-2], 'vwap'] = 99.0
    df.loc[df.index[-1], 'close'] = 100.0
    df.loc[df.index[-1], 'vwap'] = 99.0
    df.loc[df.index[-1], 'ema_9'] = 101.0
    df.loc[df.index[-1], 'ema_21'] = 99.5
    
    sig = strategy_vwap_trend.get_signal(df)
    assert sig.side == "BUY"
    assert sig.sl < df['close'].iloc[-1]
    assert sig.tp > df['close'].iloc[-1]
    assert sig.rr_ratio == 3.0
