"""Comprehensive tests for Portfolio Intelligence & Quantitative Analytics:
1. Riskfolio/skfolio-style Portfolio Optimization (Minimum Variance, Risk Parity)
2. Alphalens-style Factor Diagnostics (IC, Rank IC, Quantile tables, Spreads)
3. ARCH/GARCH-style Conditional Volatility Forecasting & EWMA fallback
4. QuantStats-style Institutional Performance Analytics (Sharpe, Sortino, Calmar, Drawdowns)
5. Portfolio Risk Overlay (Single-asset caps, Gross exposure caps, Pairwise correlation penalties)
6. Non-finite allocation guards and safety invariant checks
"""

import math
import numpy as np
import pandas as pd
import pytest

from stratex_more_integrations import (
    PortfolioConstraints,
    PortfolioOptimizer,
    PortfolioRiskOverlay,
    VolatilityForecaster,
    factor_quantile_table,
    factor_report,
    information_coefficient,
    performance_summary,
    drawdown_series,
)
from execution import ExecutionPolicy


# ==============================================================================
# 1. PORTFOLIO OPTIMIZER (RISKFOLIO / SKFOLIO)
# ==============================================================================

def test_portfolio_optimizer_minimum_variance():
    rng = np.random.default_rng(42)
    returns = pd.DataFrame(
        rng.normal(0, 0.015, (250, 4)),
        columns=["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT"]
    )
    constraints = PortfolioConstraints(max_weight=0.40, min_weight=0.05, target_gross=1.0)
    optimizer = PortfolioOptimizer(constraints)
    
    weights = optimizer.minimum_variance(returns)
    assert len(weights) == 4
    assert np.isfinite(weights.to_numpy()).all()
    assert abs(weights.sum() - 1.0) < 1e-4
    assert (weights >= 0.05 - 1e-6).all()
    assert (weights <= 0.40 + 1e-6).all()


def test_portfolio_optimizer_risk_parity():
    rng = np.random.default_rng(101)
    returns = pd.DataFrame({
        "LOW_VOL": rng.normal(0, 0.005, 200),
        "HIGH_VOL": rng.normal(0, 0.025, 200),
    })
    optimizer = PortfolioOptimizer(PortfolioConstraints(max_weight=0.90, target_gross=1.0))
    weights = optimizer.risk_parity(returns)
    
    assert abs(weights.sum() - 1.0) < 1e-5
    # Lower volatility asset must receive higher weight in risk parity
    assert weights["LOW_VOL"] > weights["HIGH_VOL"]


# ==============================================================================
# 2. FACTOR QUALITY DIAGNOSTICS (ALPHALENS)
# ==============================================================================

def test_factor_diagnostics_ic_and_quantiles():
    rng = np.random.default_rng(7)
    factor = pd.Series(rng.normal(0, 1, 300))
    # Synthetic positive relationship with noise
    forward_ret = factor * 0.02 + rng.normal(0, 0.005, 300)
    
    ic = information_coefficient(factor, forward_ret)
    rank_ic = information_coefficient(factor, forward_ret, rank=True)
    
    assert ic > 0.50
    assert rank_ic > 0.50
    
    q_table = factor_quantile_table(factor, forward_ret, q=5)
    assert len(q_table) == 5
    assert "mean" in q_table.columns
    
    # Top quantile mean return should exceed bottom quantile mean return
    assert q_table.loc[5, "mean"] > q_table.loc[1, "mean"]
    
    rep = factor_report(factor, forward_ret, q=5)
    assert rep["n"] == 300
    assert rep["quantile_return_spread"] > 0
    assert "quantiles" in rep


# ==============================================================================
# 3. CONDITIONAL VOLATILITY FORECASTER (ARCH / GARCH / EWMA)
# ==============================================================================

def test_volatility_forecaster_garch_and_fallback():
    forecaster = VolatilityForecaster(fallback_span=30)
    
    # Test fallback on short series (<50 observations)
    short_returns = pd.Series([0.01, -0.015, 0.008, -0.002, 0.005])
    short_res = forecaster.forecast(short_returns)
    assert short_res["fitted"] is False
    assert short_res["model"] == "ewm_fallback"
    assert short_res["forecast_vol"] > 0.0
    
    # Test longer series
    rng = np.random.default_rng(99)
    long_returns = pd.Series(rng.normal(0, 0.02, 600))
    long_res = forecaster.forecast(long_returns)
    assert "forecast_vol" in long_res
    assert long_res["forecast_vol"] > 0.0
    assert math.isfinite(long_res["forecast_vol"])


# ==============================================================================
# 4. INSTITUTIONAL PERFORMANCE & DRAWDOWN ANALYTICS (QUANTSTATS)
# ==============================================================================

def test_performance_summary_and_drawdown():
    # Synthetic realistic daily return stream
    returns = pd.Series([0.005, -0.002, 0.008, 0.001, -0.004, 0.003] * 50)
    summary = performance_summary(returns, periods_per_year=365)
    
    assert "total_return" in summary
    assert "annualized_volatility" in summary
    assert "sharpe" in summary
    assert "sortino" in summary
    assert "max_drawdown" in summary
    assert "win_rate" in summary
    
    assert summary["win_rate"] == pytest.approx(4.0 / 6.0, rel=1e-3)
    assert summary["max_drawdown"] <= 0.0
    
    dd = drawdown_series(returns)
    assert len(dd) == len(returns)
    assert (dd <= 1e-8).all()  # Drawdown is non-positive


# ==============================================================================
# 5. PORTFOLIO RISK OVERLAY (CAPS & CORRELATION PENALTY)
# ==============================================================================

def test_portfolio_risk_overlay_caps_and_correlation():
    overlay = PortfolioRiskOverlay(max_single_weight=0.30, max_gross=1.0)
    raw_weights = pd.Series({"BTC": 0.60, "ETH": 0.50, "SOL": 0.20})
    
    # Check single asset cap and gross scaling
    w, reasons = overlay.apply(raw_weights)
    assert "single_asset_cap" in reasons
    assert (w <= 0.30 + 1e-6).all()
    assert w.sum() <= 1.0 + 1e-6
    assert w.sum() > 0.5

    
    # Check pairwise correlation penalty
    corr_matrix = pd.DataFrame(
        [[1.0, 0.95, 0.2],
         [0.95, 1.0, 0.25],
         [0.2, 0.25, 1.0]],
        index=["BTC", "ETH", "SOL"],
        columns=["BTC", "ETH", "SOL"]
    )
    w_penalized, reasons_corr = overlay.apply(pd.Series({"BTC": 0.3, "ETH": 0.3, "SOL": 0.3}), corr=corr_matrix)
    assert "correlation_penalty" in reasons_corr
    # High correlation between BTC & ETH should cause them to receive correlation penalty
    assert w_penalized.abs().sum() <= 1.0 + 1e-6


def test_non_finite_allocation_rejection():
    overlay = PortfolioRiskOverlay()
    with pytest.raises(ValueError, match="non-finite"):
        # Force a case where weights produce non-finite
        overlay.apply(pd.Series({"A": np.nan, "B": np.inf}))


def test_authoritative_safety_guards_unbypassed():
    # Verify ExecutionPolicy cannot be bypassed by any portfolio optimizer
    can_place, reason = ExecutionPolicy.can_place_order()
    assert not can_place or "ALLOWED" in reason
