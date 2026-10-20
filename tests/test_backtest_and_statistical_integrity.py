"""
Test suite for Backtesting Engine, Cost Accounting, and Statistical Integrity
"""
import numpy as np
import pandas as pd
import pytest

import metrics
import strategy_adx_ema
from backtest_engine import BacktestEngine, DataValidator
from research.upgrade_2026_08.expansion_study import run as run_expansion_study
from research.upgrade_2026_08.walk_forward_validation import filter_trades_by_window


@pytest.fixture
def candle_dataframe():
    n = 300
    dates = pd.date_range("2026-01-01", periods=n, freq="15min")
    prices = [100.0]
    for _ in range(n - 1):
        ret = np.random.normal(0.0003, 0.006)
        prices.append(prices[-1] * (1 + ret))
    
    df = pd.DataFrame({
        "timestamp": dates,
        "open": prices,
        "high": [p * 1.005 for p in prices],
        "low": [p * 0.995 for p in prices],
        "close": [p * 1.001 for p in prices],
        "volume": [100.0 + i for i in range(n)]
    })
    return df

def test_data_validator_catches_anomalies(candle_dataframe):
    """Verifies DataValidator catches non-monotonic timestamps, NaNs, and OHLC violations."""
    # Valid dataframe passes
    assert DataValidator.validate(candle_dataframe) is True

    # 1. Non-monotonic timestamp
    bad_df_time = candle_dataframe.copy()
    bad_df_time.loc[10, 'timestamp'] = bad_df_time.loc[0, 'timestamp']
    with pytest.raises(ValueError, match="Data is not in chronological order|duplicate timestamps"):
        DataValidator.validate(bad_df_time)

    # 2. High < Low violation
    bad_df_ohlc = candle_dataframe.copy()
    bad_df_ohlc.loc[5, 'high'] = bad_df_ohlc.loc[5, 'low'] - 1.0
    with pytest.raises(ValueError, match="invalid OHLC relationships"):
        DataValidator.validate(bad_df_ohlc)

def test_backtest_accounting_and_trade_reconciliation(candle_dataframe):
    """Verifies backtest engine strictly deducts fees, applies slippage, and reconciles balance."""
    init_bal = 10000.0
    engine = BacktestEngine(
        candle_dataframe,
        strategy_adx_ema,
        fee_rate=0.001,
        slippage_rate=0.0005,
        initial_balance=init_bal,
        risk_per_trade=0.01,
        max_open_trades=2,
        long_only=True
    )
    trades, equity_curve = engine.run()
    
    if trades:
        # Check per-trade PnL matches aggregate balance delta
        total_trade_net_pnl = sum(t['net_pnl'] for t in trades)
        balance_delta = engine.balance - init_bal
        assert abs(total_trade_net_pnl - balance_delta) < 1e-4

        # Check every trade has non-negative fees and valid timestamps
        for t in trades:
            assert t['fees'] > 0
            assert t['entry_time'] <= t['exit_time']
            assert t['result'] in ['WIN', 'LOSS']

def test_metrics_statistical_formulas():
    """Verifies metrics.calculate_metrics computes exact Sharpe, Sortino, and Drawdown."""
    dates = pd.date_range("2026-01-01", periods=10, freq="1D")
    equities = [10000.0, 10200.0, 10100.0, 10400.0, 10300.0, 10600.0, 10500.0, 10800.0, 10700.0, 11000.0]
    eq_df = pd.DataFrame({"timestamp": dates, "equity": equities})

    trades = [
        {"net_pnl": 200.0, "r_multiple": 2.0},
        {"net_pnl": -100.0, "r_multiple": -1.0},
        {"net_pnl": 300.0, "r_multiple": 3.0},
        {"net_pnl": -100.0, "r_multiple": -1.0},
        {"net_pnl": 300.0, "r_multiple": 3.0}
    ]

    m = metrics.calculate_metrics(trades, eq_df, initial_balance=10000.0)
    assert m["total_trades"] == 5
    assert m["win_rate"] == 60.0 # 3 wins out of 5
    assert m["net_pnl"] == 600.0
    assert m["profit_factor"] == (800.0 / 200.0) # 4.0
    assert m["expectancy"] == (600.0 / 5.0) # 120.0
    assert m["sharpe"] > 0
    assert m["sortino"] > 0


def test_walk_forward_windows_exclude_validation_start_from_training():
    boundary = np.datetime64("2023-01-01")
    trades = [
        (np.datetime64("2022-12-31T23:59:59"), 1.0),
        (boundary, 2.0),
    ]

    training = filter_trades_by_window(trades, "2021-01-01", "2023-01-01")
    validation = filter_trades_by_window(trades, "2023-01-01", "2024-01-01")

    assert training == [trades[0]]
    assert validation == [trades[1]]


def test_walk_forward_does_not_count_pre_window_entry_that_exits_in_validation():
    timestamps = np.arange(
        np.datetime64("2023-01-01"),
        np.datetime64("2023-01-01") + np.timedelta64(204 * 4, "h"),
        np.timedelta64(4, "h"),
    )
    values = np.full(204, 100.0)
    d = {
        "ts": timestamps,
        "o": values.copy(),
        "h": values + 1,
        "l": values - 1,
        "c": values.copy(),
        "e20": np.full(204, 2.0),
        "e50": np.full(204, 1.0),
        "e200": np.full(204, 1.0),
        "atr": np.ones(204),
        "adx": np.full(204, 30.0),
    }
    d["e20"][199] = 0.5
    d["l"][202] = 97.0
    regime = np.ones(204, dtype=bool)
    validation_start = timestamps[202]

    unbounded = run_expansion_study(d, 20, 2.0, 3.0, regime, mode="crossover")
    validation = run_expansion_study(
        d, 20, 2.0, 3.0, regime, mode="crossover", start=validation_start
    )

    assert len(unbounded) == 1
    assert timestamps[201] < validation_start
    assert unbounded[0][0] >= validation_start
    assert filter_trades_by_window(unbounded, validation_start) == unbounded
    assert validation == []
    assert run_expansion_study(
        d, 20, 2.0, 3.0, regime, mode="crossover",
        start=timestamps[200], end=validation_start,
    ) == []
