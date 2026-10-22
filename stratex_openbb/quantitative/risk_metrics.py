"""
stratex_openbb/quantitative/risk_metrics.py — Quantitative Risk and Performance Metrics.
Implements OpenBB-grade mathematical calculations for Sharpe, Sortino, Calmar, VaR (95%/99%),
CVaR (Expected Shortfall), Maximum Drawdown, and Beta using NumPy and SciPy.
"""

import math
from typing import Optional, Sequence, Tuple, Union
import numpy as np
import pandas as pd

from stratex_openbb.models import QuantitativeRiskReport


def calculate_returns(prices: Union[pd.Series, np.ndarray, Sequence[float]]) -> np.ndarray:
    """Computes simple percentage returns from a price series."""
    arr = np.asarray(prices, dtype=float)
    arr = arr[~np.isnan(arr)]
    if len(arr) < 2:
        return np.array([])
    # r_t = (p_t - p_{t-1}) / p_{t-1}
    returns = np.diff(arr) / arr[:-1]
    return returns[np.isfinite(returns)]


def calculate_sharpe_ratio(
    returns: np.ndarray,
    risk_free_rate: float = 0.04,
    periods_per_year: int = 365
) -> float:
    """
    Computes annualized Sharpe Ratio: (R_annual - Rf) / Vol_annual.
    Crypto trades 365 days/year (default periods_per_year=365).
    """
    if len(returns) < 3:
        return 0.0
    rf_per_period = (1.0 + risk_free_rate) ** (1.0 / periods_per_year) - 1.0
    excess_returns = returns - rf_per_period
    std = np.std(returns, ddof=1)
    if std <= 1e-12:
        return 0.0
    mean_excess = np.mean(excess_returns)
    sharpe = (mean_excess / std) * math.sqrt(periods_per_year)
    return round(float(sharpe), 4)


def calculate_sortino_ratio(
    returns: np.ndarray,
    risk_free_rate: float = 0.04,
    periods_per_year: int = 365
) -> float:
    """
    Computes annualized Sortino Ratio: (R_annual - Rf) / Downside_Deviation.
    Downside deviation evaluates variance of negative returns only.
    """
    if len(returns) < 3:
        return 0.0
    rf_per_period = (1.0 + risk_free_rate) ** (1.0 / periods_per_year) - 1.0
    downside_diff = returns - rf_per_period
    downside_returns = downside_diff[downside_diff < 0.0]
    if len(downside_returns) == 0:
        return 10.0  # Zero downside risk cap
    downside_std = math.sqrt(np.mean(downside_returns ** 2))
    if downside_std <= 1e-12:
        return 0.0
    mean_excess = np.mean(returns - rf_per_period)
    sortino = (mean_excess / downside_std) * math.sqrt(periods_per_year)
    return round(float(sortino), 4)


def calculate_max_drawdown(prices_or_cum: Union[np.ndarray, Sequence[float]]) -> float:
    """
    Computes maximum peak-to-trough percentage drawdown.
    Returns a positive percentage (e.g. 0.15 = 15% drawdown).
    """
    arr = np.asarray(prices_or_cum, dtype=float)
    if len(arr) < 2:
        return 0.0
    peaks = np.maximum.accumulate(arr)
    # Prevent divide by zero
    valid = peaks > 0
    if not np.any(valid):
        return 0.0
    drawdowns = (peaks[valid] - arr[valid]) / peaks[valid]
    max_dd = np.max(drawdowns)
    return round(float(max_dd), 4)


def calculate_calmar_ratio(
    returns: np.ndarray,
    max_dd: float,
    periods_per_year: int = 365
) -> float:
    """Computes Calmar Ratio: Annualized Return / Max Drawdown."""
    if max_dd <= 1e-6 or len(returns) < 3:
        return 0.0
    ann_return = np.mean(returns) * periods_per_year
    calmar = ann_return / max_dd
    return round(float(calmar), 4)


def calculate_var_historical(returns: np.ndarray, confidence_level: float = 0.95) -> float:
    """
    Historical Value at Risk (VaR).
    Returns positive value representing maximum expected single-period loss.
    e.g. 0.035 means 95% confident loss does not exceed 3.5%.
    """
    if len(returns) < 5:
        return 0.0
    percentile = (1.0 - confidence_level) * 100.0
    var_val = -np.percentile(returns, percentile)
    return round(float(max(0.0, var_val)), 4)


def calculate_var_parametric(returns: np.ndarray, confidence_level: float = 0.95) -> float:
    """
    Parametric Gaussian Value at Risk: -(mu - z * sigma).
    Uses standard normal z-scores: 1.64485 for 95%, 2.32635 for 99%.
    """
    if len(returns) < 5:
        return 0.0
    mu = np.mean(returns)
    sigma = np.std(returns, ddof=1)
    # Norm inverse z-scores
    if confidence_level >= 0.99:
        z = 2.32635
    elif confidence_level >= 0.95:
        z = 1.64485
    else:
        z = 1.28155  # 90%
    var_val = -(mu - z * sigma)
    return round(float(max(0.0, var_val)), 4)


def calculate_cvar(returns: np.ndarray, confidence_level: float = 0.95) -> float:
    """
    Conditional Value at Risk (CVaR / Expected Shortfall).
    Expected loss given that returns fall below the VaR threshold.
    """
    if len(returns) < 5:
        return 0.0
    percentile = (1.0 - confidence_level) * 100.0
    cutoff = np.percentile(returns, percentile)
    tail = returns[returns <= cutoff]
    if len(tail) == 0:
        return calculate_var_historical(returns, confidence_level)
    cvar_val = -np.mean(tail)
    return round(float(max(0.0, cvar_val)), 4)


def calculate_beta(asset_returns: np.ndarray, benchmark_returns: np.ndarray) -> Optional[float]:
    """Computes asset Beta relative to benchmark: Cov(A, B) / Var(B)."""
    min_len = min(len(asset_returns), len(benchmark_returns))
    if min_len < 10:
        return None
    a = asset_returns[-min_len:]
    b = benchmark_returns[-min_len:]
    var_b = np.var(b, ddof=1)
    if var_b <= 1e-12:
        return None
    cov = np.cov(a, b)[0][1]
    beta = cov / var_b
    return round(float(beta), 4)


def compute_quantitative_risk_report(
    prices: Union[pd.Series, np.ndarray, Sequence[float]],
    symbol: str = "ASSET",
    benchmark_prices: Optional[Union[pd.Series, np.ndarray, Sequence[float]]] = None,
    risk_free_rate: float = 0.04,
    periods_per_year: int = 365
) -> QuantitativeRiskReport:
    """
    Generates a full OpenBB-grade quantitative risk report for an asset's price series.
    """
    arr = np.asarray(prices, dtype=float)
    arr = arr[~np.isnan(arr)]
    returns = calculate_returns(arr)

    if len(returns) < 5:
        return QuantitativeRiskReport(
            symbol=symbol,
            period_days=len(arr),
            annualized_return=0.0,
            annualized_volatility=0.0,
            sharpe_ratio=0.0,
            sortino_ratio=0.0,
            calmar_ratio=0.0,
            max_drawdown=0.0,
            var_95_historical=0.0,
            var_99_historical=0.0,
            var_95_parametric=0.0,
            var_99_parametric=0.0,
            cvar_95=0.0,
            cvar_99=0.0,
            sample_size=len(arr)
        )

    ann_return = round(float(np.mean(returns) * periods_per_year), 4)
    ann_vol = round(float(np.std(returns, ddof=1) * math.sqrt(periods_per_year)), 4)
    max_dd = calculate_max_drawdown(arr)
    sharpe = calculate_sharpe_ratio(returns, risk_free_rate, periods_per_year)
    sortino = calculate_sortino_ratio(returns, risk_free_rate, periods_per_year)
    calmar = calculate_calmar_ratio(returns, max_dd, periods_per_year)

    var95_h = calculate_var_historical(returns, 0.95)
    var99_h = calculate_var_historical(returns, 0.99)
    var95_p = calculate_var_parametric(returns, 0.95)
    var99_p = calculate_var_parametric(returns, 0.99)

    cvar95 = calculate_cvar(returns, 0.95)
    cvar99 = calculate_cvar(returns, 0.99)

    # Beta to benchmark if supplied
    beta = None
    if benchmark_prices is not None:
        bm_returns = calculate_returns(benchmark_prices)
        beta = calculate_beta(returns, bm_returns)

    # Distribution skewness and kurtosis
    from scipy import stats
    skew = round(float(stats.skew(returns)), 4) if len(returns) > 3 else 0.0
    kurt = round(float(stats.kurtosis(returns)), 4) if len(returns) > 3 else 0.0

    return QuantitativeRiskReport(
        symbol=symbol,
        period_days=len(arr),
        annualized_return=ann_return,
        annualized_volatility=ann_vol,
        sharpe_ratio=sharpe,
        sortino_ratio=sortino,
        calmar_ratio=calmar,
        max_drawdown=max_dd,
        var_95_historical=var95_h,
        var_99_historical=var99_h,
        var_95_parametric=var95_p,
        var_99_parametric=var99_p,
        cvar_95=cvar95,
        cvar_99=cvar99,
        beta_to_benchmark=beta,
        skewness=skew,
        kurtosis=kurt,
        sample_size=len(arr)
    )
