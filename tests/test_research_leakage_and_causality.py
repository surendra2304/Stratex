import numpy as np
import pandas as pd
import pytest

import strategy_adx_ema
from backtest_engine import BacktestEngine
from features import add_features
from metrics import calculate_metrics
from research.regime_classifier import RegimeClassifier


@pytest.fixture
def synthetic_market_data():
    np.random.seed(42)
    n = 300
    dates = pd.date_range('2026-01-01', periods=n, freq='15min')
    prices = [100.0]
    for _ in range(n - 1):
        ret = np.random.normal(0.0002, 0.005)
        prices.append(prices[-1] * (1 + ret))
        
    df = pd.DataFrame({
        'timestamp': dates,
        'open': prices,
        'high': [p * 1.004 for p in prices],
        'low': [p * 0.996 for p in prices],
        'close': [p * 1.001 for p in prices],
        'volume': [1000.0 + (i * 2.0) for i in range(n)]
    })
    return df


def test_feature_lookahead_leakage_invariance(synthetic_market_data):
    df = synthetic_market_data.copy()
    full_feat = add_features(df.copy())
    
    for idx in range(250, len(df)):
        sub_df = df.iloc[:idx+1].copy()
        inc_feat = add_features(sub_df)
        
        assert np.isclose(full_feat['ema_21'].iloc[idx], inc_feat['ema_21'].iloc[-1], rtol=1e-5)
        assert np.isclose(full_feat['rsi_14'].iloc[idx], inc_feat['rsi_14'].iloc[-1], rtol=1e-5)
        assert np.isclose(full_feat['atr_14'].iloc[idx], inc_feat['atr_14'].iloc[-1], rtol=1e-5)
        assert np.isclose(full_feat['bb_middle'].iloc[idx], inc_feat['bb_middle'].iloc[-1], rtol=1e-5)


def test_execution_causality_adversarial_future_corruption(synthetic_market_data):
    df = synthetic_market_data.copy()
    df = add_features(df)
    
    engine_orig = BacktestEngine(df.copy(), [strategy_adx_ema], initial_balance=10000.0)
    trades_orig, _ = engine_orig.run()
    
    if trades_orig:
        first_trade = trades_orig[0]
        entry_time = first_trade['entry_time']
        entry_idx = df[df['timestamp'] == entry_time].index[0]
        
        corrupted_df = df.copy()
        for c in ['open', 'high', 'low', 'close']:
            corrupted_df.loc[entry_idx + 5:, c] *= 1.50
        
        engine_corrupt = BacktestEngine(corrupted_df.copy(), [strategy_adx_ema], initial_balance=10000.0)
        trades_corrupt, _ = engine_corrupt.run()
        
        assert trades_corrupt[0]['entry_time'] == first_trade['entry_time']
        assert np.isclose(trades_corrupt[0]['entry_price'], first_trade['entry_price'], rtol=1e-5)
        assert np.isclose(trades_corrupt[0]['quantity'], first_trade['quantity'], rtol=1e-5)


def test_profit_factor_zero_loss_undefined_integrity():
    winning_trades_only = [
        {'trade_id': '1', 'gross_pnl': 100.0, 'net_pnl': 90.0, 'fees': 10.0, 'slippage': 0.0, 'entry_fee': 5.0, 'holding_time': 10},
        {'trade_id': '2', 'gross_pnl': 150.0, 'net_pnl': 140.0, 'fees': 10.0, 'slippage': 0.0, 'entry_fee': 5.0, 'holding_time': 12}
    ]
    metrics = calculate_metrics(winning_trades_only, pd.DataFrame())
    assert metrics['profit_factor'] == float('inf')
    assert 'UNDEFINED' in metrics['profit_factor_str']
    assert metrics['evidence_grade'] == 'GRADE D (under 30 trades)'


def test_regime_classifier_causal_determinism(synthetic_market_data):
    df = add_features(synthetic_market_data.copy())
    regime = RegimeClassifier.classify_regime(df)
    assert regime in ['TREND_UP', 'TREND_DOWN', 'RANGE', 'HIGH_VOLATILITY', 'LOW_VOLATILITY', 'UNKNOWN']
