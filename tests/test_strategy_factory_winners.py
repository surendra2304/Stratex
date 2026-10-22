import numpy as np
import pandas as pd

import strategy_factory_winners


def _generate_synthetic_candles(n_bars=60, base_price=100.0, trend="BULLISH"):
    np.random.seed(42)
    timestamps = pd.date_range("2026-08-24 12:00:00", periods=n_bars, freq="5min")
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


def test_factory_winner_1_signal():
    df = _generate_synthetic_candles(60, trend="BULLISH")
    df = strategy_factory_winners.add_features(df)
    
    # Simulate MACD cross up below middle band (dip buying in trend)
    df.loc[df.index[-2], 'macd'] = -0.5
    df.loc[df.index[-2], 'macd_signal'] = -0.2
    
    df.loc[df.index[-1], 'macd'] = 0.1
    df.loc[df.index[-1], 'macd_signal'] = -0.1
    df.loc[df.index[-1], 'close'] = df.loc[df.index[-1], 'bb_mid'] - 1.0  # below mid
    
    sig = strategy_factory_winners.get_signal_winner_1(df)
    assert sig.side == "BUY"
    assert sig.sl < df['close'].iloc[-1]
    assert sig.tp > df['close'].iloc[-1]
    assert sig.rr_ratio == 2.0


def test_factory_winner_3_signal():
    df = _generate_synthetic_candles(60, trend="BEARISH")
    df = strategy_factory_winners.add_features(df)
    
    # Simulate MACD cross down above middle band (rally selling in trend)
    df.loc[df.index[-2], 'macd'] = 0.5
    df.loc[df.index[-2], 'macd_signal'] = 0.2
    
    df.loc[df.index[-1], 'macd'] = -0.1
    df.loc[df.index[-1], 'macd_signal'] = 0.1
    df.loc[df.index[-1], 'close'] = df.loc[df.index[-1], 'bb_mid'] + 1.0  # above mid
    
    sig = strategy_factory_winners.get_signal_winner_3(df)
    assert sig.side == "SELL"
    assert sig.sl > df['close'].iloc[-1]
    assert sig.tp < df['close'].iloc[-1]
    assert sig.rr_ratio == 3.0


def test_factory_winner_5_signal():
    df = _generate_synthetic_candles(60, trend="BULLISH")
    df = strategy_factory_winners.add_features(df)
    
    # Simulate MACD cross up below middle band
    df.loc[df.index[-2], 'macd'] = -0.5
    df.loc[df.index[-2], 'macd_signal'] = -0.2
    
    df.loc[df.index[-1], 'macd'] = 0.1
    df.loc[df.index[-1], 'macd_signal'] = -0.1
    df.loc[df.index[-1], 'close'] = df.loc[df.index[-1], 'bb_mid'] - 1.0
    
    sig = strategy_factory_winners.get_signal_winner_5(df)
    assert sig.side == "BUY"
    assert sig.sl < df['close'].iloc[-1]
    assert sig.tp > df['close'].iloc[-1]
    assert sig.rr_ratio == 3.0
