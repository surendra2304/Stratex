# tests/test_quantum_validation.py
"""Comprehensive unit and integration tests for the Quantum Validation Framework."""

import pytest
import numpy as np
import pandas as pd

from quantum.validation.data import validate_dataset, inspect_dataset_file, load_benchmark_data
from quantum.validation.splits import generate_walk_forward_splits, WalkForwardFold
from quantum.validation.baselines import ClassicalRuleBasedStrategy, ClassicalMLStrategy
from quantum.validation.quantum_models import QuantumVQCModel, HybridQuantumClassifier, QuantumPortfolioOptimizer
from quantum.validation.backtest import BacktestRunner, TradeRecord
from quantum.validation.metrics import calculate_performance_metrics
from quantum.validation.bootstrap import run_paired_bootstrap
from quantum.validation.benchmark import run_full_benchmark

def _create_synthetic_ohlcv(n_bars: int = 200) -> pd.DataFrame:
    """Generates synthetic OHLCV dataframe for deterministic testing."""
    rng = np.random.default_rng(42)
    dates = pd.date_range("2026-08-01", periods=n_bars, freq="15min")
    price = 60000.0
    records = []
    for d in dates:
        ret = rng.normal(0, 0.002)
        price *= (1 + ret)
        high = price * (1 + abs(rng.normal(0, 0.001)))
        low = price * (1 - abs(rng.normal(0, 0.001)))
        open_p = price * (1 + rng.normal(0, 0.0005))
        vol = float(rng.uniform(10.0, 100.0))
        records.append({
            "timestamp": d,
            "open": open_p,
            "high": max(open_p, high, price),
            "low": min(open_p, low, price),
            "close": price,
            "volume": vol
        })
    return pd.DataFrame(records)

def test_dataset_validator():
    df = _create_synthetic_ohlcv(100)
    assert validate_dataset(df) is True
    
    # Missing columns
    assert validate_dataset(df.drop(columns=['close'])) is False
    # Empty df
    assert validate_dataset(pd.DataFrame()) is False

def test_walk_forward_splits():
    df = _create_synthetic_ohlcv(500)
    folds, err = generate_walk_forward_splits(df, n_folds=3, allow_proportional_fallback=True)
    assert len(folds) == 3
    assert err is None
    for f in folds:
        assert f.train_rows > 0
        assert f.val_rows > 0
        assert f.test_rows > 0
        # Check chronological ordering
        assert pd.to_datetime(f.train_end) <= pd.to_datetime(f.val_start)
        assert pd.to_datetime(f.val_end) <= pd.to_datetime(f.test_start)

def test_classical_strategies():
    df = _create_synthetic_ohlcv(150)
    rule_strat = ClassicalRuleBasedStrategy()
    rule_strat.fit(df)
    sig_rule = rule_strat.generate_signal(df)
    assert "signal" in sig_rule
    assert sig_rule["signal"] in ["BUY", "SELL", "HOLD"]

    ml_strat = ClassicalMLStrategy()
    ml_strat.fit(df)
    sig_ml = ml_strat.generate_signal(df)
    assert "signal" in sig_ml

def test_quantum_models():
    df = _create_synthetic_ohlcv(150)
    vqc = QuantumVQCModel()
    vqc.fit(df)
    sig_vqc = vqc.generate_signal(df)
    assert "signal" in sig_vqc
    
    hybrid = HybridQuantumClassifier()
    hybrid.fit(df)
    sig_hybrid = hybrid.generate_signal(df)
    assert "signal" in sig_hybrid

    opt = QuantumPortfolioOptimizer()
    candidates = [
        {"signal": "BUY", "confidence": 0.8, "entry": 60000.0, "atr": 200.0},
        {"signal": "BUY", "confidence": 0.6, "entry": 60000.0, "atr": 500.0}
    ]
    selected = opt.select_best_opportunities(candidates, max_slots=1)
    assert len(selected) == 1
    assert selected[0]["confidence"] == 0.8

def test_backtest_runner_and_metrics():
    df = _create_synthetic_ohlcv(150)
    runner = BacktestRunner(initial_capital=10000.0)
    strat = ClassicalRuleBasedStrategy()
    res = runner.run_strategy(strat, df, fold_idx=1)
    assert res.strategy_name == "Classical_Rule_Based"
    assert len(res.equity_curve) > 0

def test_bootstrap_statistics():
    returns_q = [0.01, -0.005, 0.02, 0.015, -0.01]
    returns_c = [0.005, -0.01, 0.01, 0.005, -0.015]
    boot = run_paired_bootstrap(returns_q, returns_c, n_iterations=1000, seed=42)
    assert boot.iterations == 1000
    assert boot.mean_difference > 0
    assert len(str(boot.ci_95_lower)) > 0
    assert len(str(boot.ci_95_upper)) > 0

def test_execution_isolation_safety():
    """Verify that the quantum validation module does not expose any order execution hooks."""
    from quantum.validation import QuantumVQCModel, HybridQuantumClassifier, QuantumPortfolioOptimizer
    vqc = QuantumVQCModel()
    hybrid = HybridQuantumClassifier()
    opt = QuantumPortfolioOptimizer()
    
    # Assert no execution/order functions exist
    for obj in [vqc, hybrid, opt]:
        assert not hasattr(obj, "place_order")
        assert not hasattr(obj, "cancel_order")
        assert not hasattr(obj, "execute_trade")
        assert not hasattr(obj, "set_risk_limit")
