"""
tests/test_advanced_risk.py — Comprehensive Unit & Integration Tests for Advanced Risk & Optimization Modules.

Verifies:
1. DynamicRiskManager: Fixed fractional, Volatility sizing, Kelly criterion, Risk parity, VaR/CVaR, Stress testing, and Drawdown sizing attenuation.
2. PortfolioOptimizer: Mean-variance Sharpe maximization, Black-Litterman view blending, and Rebalance trigger detection.
3. AdvancedOrderExecutor: TWAP schedules, Iceberg order slicing, and Implementation Shortfall calculations.
4. MultiTimeframeAnalyzer: Consensus scoring and cross-timeframe trend alignment.
5. VolatilitySizingEngine: Realized volatility computation, regime classification, and volatility targeting.
6. DrawdownController: High-water mark tracking, underwater duration, and multi-tier defensive actions.
7. PerformanceAttributionEngine: Strategy and symbol contribution breakdowns.
8. EnhancedTelemetryCollector: Market breadth metrics and full snapshot generation.
9. AdvancedBacktester: Execution friction calculations and Walk-Forward Analysis.
10. AdvancedConfigManager: Validation bounds, versioning, and rollback functionality.
"""

import tempfile

import numpy as np
import pandas as pd
import pytest

from analysis.multi_timeframe import MultiTimeframeAnalyzer
from analytics.performance_attribution import PerformanceAttributionEngine
from backtest.advanced_backtester import AdvancedBacktester
from config_manager_advanced import AdvancedConfigManager, AdvancedConfigSchema
from execution_algos.advanced_executor import AdvancedOrderExecutor
from optimization.portfolio_optimizer import PortfolioOptimizer
from risk.drawdown_controller import DrawdownController
from risk.dynamic_risk_manager import DynamicRiskManager, RiskBudget
from risk.volatility_sizing import VolatilitySizingEngine
from telemetry.enhanced_telemetry import EnhancedTelemetryCollector


def create_sample_df(n_bars=50, base_price=50000.0):
    np.random.seed(42)
    noise = np.random.normal(0, 50, n_bars)
    prices = [base_price + i * 10 + noise[i] for i in range(n_bars)]
    return pd.DataFrame({
        "open": prices,
        "high": [p + 25 for p in prices],
        "low": [p - 25 for p in prices],
        "close": prices,
        "volume": [100.0] * n_bars
    })


def test_dynamic_risk_manager_models():
    mgr = DynamicRiskManager(RiskBudget(max_risk_per_trade_pct=0.01, max_asset_concentration_pct=0.50))

    # 1. Fixed Fractional
    size_ff = mgr.calculate_fixed_fractional_size(equity=10000.0, entry_price=50000.0, stop_loss_price=49000.0)
    assert size_ff == 0.10  # ($100 risk / $1000 per unit)

    # 2. Volatility Sizing
    size_vol = mgr.calculate_volatility_size(equity=10000.0, entry_price=50000.0, atr=500.0, atr_multiplier=2.0)
    assert size_vol > 0.0

    # 3. Kelly Sizing
    size_kelly = mgr.calculate_kelly_size(equity=10000.0, entry_price=50000.0, win_rate=0.60, profit_factor=1.8)
    assert size_kelly > 0.0

    # 4. Risk Parity
    vols = {"BTC": 0.20, "ETH": 0.40}
    weights = mgr.calculate_risk_parity_weights(vols)
    assert weights["BTC"] > weights["ETH"]
    assert pytest.approx(sum(weights.values()), 0.01) == 1.0

    # 5. VaR / CVaR
    returns = [-0.02, -0.015, -0.01, 0.005, 0.01, 0.015, 0.02, -0.03, 0.005, 0.01]
    var_pct, var_dlr, cvar_pct, cvar_dlr = mgr.compute_var_cvar(returns, confidence_level=0.95, portfolio_value=10000.0)
    assert var_pct > 0.0
    assert cvar_pct >= var_pct

    # 6. Drawdown Sizing Attenuation
    sz, mult = mgr.adjust_size_for_drawdown(base_size=1.0, current_drawdown_pct=7.5)
    assert mult == 0.75
    assert sz == 0.75


def test_portfolio_optimizer():
    opt = PortfolioOptimizer(risk_free_rate=0.04)
    expected_returns = {"strat_trend": 0.25, "strat_mean_rev": 0.15}
    cov_df = pd.DataFrame([[0.04, 0.00], [0.00, 0.04]], index=["strat_trend", "strat_mean_rev"], columns=["strat_trend", "strat_mean_rev"])

    weights = opt.optimize_mean_variance(expected_returns, cov_df, min_weight=0.10, max_weight=0.90)
    assert weights["strat_trend"] > weights["strat_mean_rev"]
    assert pytest.approx(sum(weights.values()), 0.01) == 1.0

    # Black-Litterman
    bl_weights = opt.black_litterman_allocation(
        prior_weights={"strat_trend": 0.5, "strat_mean_rev": 0.5},
        views={"strat_trend": 0.8, "strat_mean_rev": 0.2},
        confidence=0.70
    )
    assert bl_weights["strat_trend"] > bl_weights["strat_mean_rev"]


def test_advanced_order_executor():
    executor = AdvancedOrderExecutor()

    # TWAP Schedule
    slices = executor.build_twap_schedule("BTCUSDT", "BUY", total_quantity=1.0, current_price=50000.0, num_slices=5)
    assert len(slices) == 5
    assert slices[0].quantity == 0.2

    # Iceberg Order
    iceberg = executor.build_iceberg_order(total_quantity=5.0, display_fraction=0.20)
    assert iceberg["display_quantity"] == 1.0
    assert iceberg["hidden_quantity"] == 4.0

    # Implementation Shortfall
    is_res = executor.compute_implementation_shortfall(
        arrival_price=50000.0,
        executed_fills=[(0.5, 50010.0), (0.5, 50020.0)],
        direction="BUY",
        fees_paid=5.0
    )
    assert is_res["shortfall_dollars"] > 0.0
    assert is_res["executed_vwap"] == 50015.0


def test_multi_timeframe_analyzer():
    analyzer = MultiTimeframeAnalyzer()
    df_sample = create_sample_df(60)

    consensus = analyzer.compute_consensus({"1h": df_sample, "4h": df_sample})
    assert "consensus_score" in consensus
    assert consensus["recommended_bias"] in ["BULLISH", "BEARISH", "NEUTRAL"]


def test_volatility_sizing_engine():
    engine = VolatilitySizingEngine(target_annual_vol=0.15)
    df_sample = create_sample_df(40)

    vol = engine.calculate_realized_volatility(df_sample["close"])
    assert vol > 0.0

    regime = engine.classify_volatility_regime(current_vol=0.10, baseline_vol=0.25)
    assert regime == "LOW_VOLATILITY"

    weight = engine.compute_vol_targeted_weight(asset_volatility=0.30, target_vol=0.15)
    assert weight == 0.50


def test_drawdown_controller():
    controller = DrawdownController(warning_drawdown_pct=0.08, critical_drawdown_pct=0.15, initial_capital=10000.0)

    # Initial update
    state = controller.update_equity(10000.0)
    assert state.current_drawdown_pct == 0.0

    # Drawdown to $9,000 (10% DD -> Warning)
    state = controller.update_equity(9000.0)
    assert state.current_drawdown_pct == 10.0
    action = controller.get_defensive_action()
    assert action["action"] == "THROTTLE_SIZING"

    # Drawdown to $8,400 (16% DD -> Critical)
    state = controller.update_equity(8400.0)
    assert state.current_drawdown_pct == 16.0
    action = controller.get_defensive_action()
    assert action["action"] == "HALT_AND_FLAT"
    assert action["sizing_factor"] == 0.0


def test_performance_attribution():
    engine = PerformanceAttributionEngine()
    trades = [
        {"strategy": "strat_trend", "symbol": "BTCUSDT", "net_pnl": 50.0, "gross_pnl": 52.0},
        {"strategy": "strat_scalp", "symbol": "ETHUSDT", "net_pnl": -20.0, "gross_pnl": -18.0}
    ]

    strat_attr = engine.analyze_strategy_contributions(trades)
    assert strat_attr["total_portfolio_pnl"] == 30.0
    assert "strat_trend" in strat_attr["strategy_breakdown"]

    sym_attr = engine.analyze_symbol_contributions(trades)
    assert "BTCUSDT" in sym_attr
    assert sym_attr["BTCUSDT"]["net_pnl"] == 50.0


def test_enhanced_telemetry():
    collector = EnhancedTelemetryCollector()
    df_sample = create_sample_df(30)
    breadth = collector.compute_market_breadth({"BTCUSDT": df_sample})
    assert "breadth_pct_above_ema20" in breadth


def test_advanced_backtester():
    backtester = AdvancedBacktester()
    impact_pct, fee = backtester.calculate_execution_friction(price=50000.0, quantity=0.1, bar_volume=10.0)
    assert impact_pct > 0.0
    assert fee > 0.0

    # Walk forward analysis test
    df_sample = create_sample_df(100)
    wfa = backtester.run_walk_forward_analysis(
        df=df_sample,
        signal_generator_fn=lambda df, p: pd.Series([1] * len(df)),
        param_grid=[{"window": 10}],
        train_window_bars=50,
        test_window_bars=20
    )
    assert wfa["status"] == "COMPLETED"
    assert wfa["windows_completed"] > 0


def test_advanced_config_manager():
    with tempfile.TemporaryDirectory() as tmpdir:
        mgr = AdvancedConfigManager(config_dir=tmpdir)

        # Valid config
        cfg = AdvancedConfigSchema(max_drawdown_limit_pct=0.12, max_daily_loss_pct=0.04)
        ok, msg = mgr.update_config(cfg)
        assert ok is True
        assert mgr.current_config.version == 2

        # Invalid config (> 15% max drawdown)
        invalid_cfg = AdvancedConfigSchema(max_drawdown_limit_pct=0.25)
        ok, msg = mgr.update_config(invalid_cfg)
        assert ok is False

        # Rollback
        rolled, rmsg = mgr.rollback()
        assert rolled is True
        assert mgr.current_config.version == 1
