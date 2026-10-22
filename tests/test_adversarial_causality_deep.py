import numpy as np
import pandas as pd
import pytest

import strategy_adx_ema
from backtest_engine import BacktestEngine
from features import add_features


@pytest.fixture
def multi_step_market_data():
    np.random.seed(123)
    n = 350
    dates = pd.date_range('2026-01-01', periods=n, freq='15min')
    prices = [100.0]
    for _ in range(n - 1):
        ret = np.random.normal(0.0001, 0.004)
        prices.append(prices[-1] * (1 + ret))
        
    df = pd.DataFrame({
        'timestamp': dates,
        'open': prices,
        'high': [p * 1.003 for p in prices],
        'low': [p * 0.997 for p in prices],
        'close': [p * 1.001 for p in prices],
        'volume': [1000.0 + (i * 1.5) for i in range(n)]
    })
    return df


@pytest.mark.parametrize('corrupt_offset', [1, 2, 3, 5, 10, 20])
def test_multi_step_adversarial_prefix_invariance(multi_step_market_data, corrupt_offset):
    df = multi_step_market_data.copy()
    df = add_features(df)
    
    engine_orig = BacktestEngine(df.copy(), [strategy_adx_ema], initial_balance=10000.0)
    trades_orig, eq_orig = engine_orig.run()
    
    if len(trades_orig) >= 2:
        # Pick boundary at second trade entry
        t2_time = trades_orig[1]['entry_time']
        t2_idx = df[df['timestamp'] == t2_time].index[0]
        corrupt_idx = t2_idx + corrupt_offset
        
        if corrupt_idx < len(df) - 5:
            corrupted_df = df.copy()
            for c in ['open', 'high', 'low', 'close']:
                corrupted_df.loc[corrupt_idx:, c] *= 1.35
            
            engine_corrupt = BacktestEngine(corrupted_df.copy(), [strategy_adx_ema], initial_balance=10000.0)
            trades_corrupt, eq_corrupt = engine_corrupt.run()
            
            # Verify ALL trades that occurred before corrupt_idx are 100% identical in timestamp, price, qty, SL, TP
            orig_prefix = [t for t in trades_orig if df[df['timestamp'] == t['entry_time']].index[0] < corrupt_idx]
            corrupt_prefix = [t for t in trades_corrupt if df[df['timestamp'] == t['entry_time']].index[0] < corrupt_idx]
            
            assert len(orig_prefix) == len(corrupt_prefix)
            for t_o, t_c in zip(orig_prefix, corrupt_prefix):
                assert t_o['entry_time'] == t_c['entry_time']
                assert np.isclose(t_o['entry_price'], t_c['entry_price'], rtol=1e-5)
                assert np.isclose(t_o['quantity'], t_c['quantity'], rtol=1e-5)
                assert np.isclose(t_o['stop_loss'], t_c['stop_loss'], rtol=1e-5)
                assert np.isclose(t_o['take_profit'], t_c['take_profit'], rtol=1e-5)
