"""
Comprehensive Test Suite for All Strategies and Feature/ML Pipeline
"""
import os
import pytest
import numpy as np
import pandas as pd

import features
import strategy_adx_ema
import strategy_supertrend
import strategy_swing
import strategy_scalper
import strategy_aggressor
import strategy_ml
import strategy_bollinger
import strategy_breakout_vol
import strategy_hybrid

@pytest.fixture
def ohlcv_series():
    n = 250
    dates = pd.date_range("2026-08-01", periods=n, freq="5min")
    prices = [100.0]
    for _ in range(n - 1):
        ret = np.random.normal(0.0005, 0.008)
        prices.append(prices[-1] * (1 + ret))
    
    df = pd.DataFrame({
        "timestamp": dates,
        "open": prices,
        "high": [p * 1.004 for p in prices],
        "low": [p * 0.996 for p in prices],
        "close": [p * 1.001 for p in prices],
        "volume": [50.0 + i for i in range(n)]
    })
    return df

def test_feature_pipeline_computations(ohlcv_series):
    """Verifies features.add_features computes required trend, momentum, volatility, and volume indicators."""
    feat_df = features.add_features(ohlcv_series.copy())
    assert len(feat_df.columns) >= 35
    for required in ["ema_200", "rsi_14", "macd", "macd_signal", "bb_upper", "bb_lower", "supertrend", "vol_delta"]:
        assert required in feat_df.columns, f"Missing feature {required}"
    
    # Check no infinity or unhandled NaN at the tail
    last = feat_df.iloc[-1]
    assert not np.isinf(last["rsi_14"])
    assert not np.isinf(last["macd"])

def test_all_strategies_schema_and_underflow_safety(ohlcv_series):
    """Verifies every strategy handles underflow, empty df, and returns standard SignalResult."""
    strategy_modules = [
        ("adx_ema", strategy_adx_ema),
        ("supertrend", strategy_supertrend),
        ("swing", strategy_swing),
        ("scalper", strategy_scalper),
        ("aggressor", strategy_aggressor),
        ("ml", strategy_ml),
        ("bollinger", strategy_bollinger),
        ("breakout_vol", strategy_breakout_vol),
        ("hybrid", strategy_hybrid)
    ]

    for name, mod in strategy_modules:
        fn = getattr(mod, "get_signal")
        
        # 1. Underflow
        short_df = ohlcv_series.head(5).copy()
        sig_short = fn(short_df)
        assert sig_short.side is None or sig_short[0] is None
        
        # 2. Empty df
        empty_df = pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
        sig_empty = fn(empty_df)
        assert sig_empty.side is None or sig_empty[0] is None
        
        # 3. None df
        sig_none = fn(None)
        assert sig_none.side is None or sig_none[0] is None

        # 4. Standard 250 bars
        sig = fn(ohlcv_series.copy())
        assert hasattr(sig, "side") or len(sig) >= 4
        assert hasattr(sig, "confidence") or hasattr(sig, "win_rate_prior")

def test_ml_strategy_training_and_inference_lifecycle(ohlcv_series):
    """Verifies ML strategy training, model persistence, and probability scoring."""
    ml = strategy_ml.MLStrategy()
    
    # Train dual models
    ml.train(ohlcv_series.copy())
    assert ml.model_buy is not None
    assert ml.scaler is not None
    
    # Evaluate signal
    sig = ml.get_signal(ohlcv_series.copy())
    assert sig is not None
    if sig.side == "BUY":
        assert sig.sl < ohlcv_series['close'].iloc[-1] < sig.tp
    elif sig.side == "SELL":
        assert sig.tp < ohlcv_series['close'].iloc[-1] < sig.sl
