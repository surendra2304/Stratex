"""
stratex_openbb/quantitative/volatility_estimators.py — Advanced Quantitative Volatility Estimators.
Implements OpenBB-grade multi-bar volatility estimators:
- Realized Close-to-Close Volatility
- Parkinson Volatility (High-Low estimator)
- Garman-Klass Volatility (OHLC estimator)
- Yang-Zhang Volatility (Unbiased overnight-jump + drift estimator)
"""

import datetime
import math
from typing import Optional, Union
import numpy as np
import pandas as pd

from stratex_openbb.models import VolatilityEstimates


def compute_parkinson_volatility(
    high: np.ndarray,
    low: np.ndarray,
    periods_per_year: int = 365
) -> float:
    """
    Computes annualized Parkinson Volatility (High-Low estimator).
    5x more sample-efficient than standard close-to-close volatility.
    sigma_P = sqrt( (N / (4 * ln(2) * n)) * sum( (ln(H_i / L_i))^2 ) )
    """
    n = len(high)
    if n < 3:
        return 0.0
    # Guard against zero or non-positive
    valid = (high > 0) & (low > 0) & (high >= low)
    if not np.any(valid):
        return 0.0
    h = high[valid]
    l = low[valid]
    log_hl = np.log(h / l)
    factor = periods_per_year / (4.0 * math.log(2.0) * len(h))
    variance = factor * np.sum(log_hl ** 2)
    return round(float(math.sqrt(max(0.0, variance))), 4)


def compute_garman_klass_volatility(
    open_: np.ndarray,
    high: np.ndarray,
    low: np.ndarray,
    close: np.ndarray,
    periods_per_year: int = 365
) -> float:
    """
    Computes annualized Garman-Klass Volatility (OHLC estimator).
    8x more sample-efficient than close-to-close volatility.
    sigma_GK = sqrt( (N / n) * sum( 0.5 * (ln(H/L))^2 - (2*ln(2) - 1) * (ln(C/O))^2 ) )
    """
    n = len(close)
    if n < 3:
        return 0.0
    valid = (open_ > 0) & (high > 0) & (low > 0) & (close > 0) & (high >= low)
    if not np.any(valid):
        return 0.0
    o = open_[valid]
    h = high[valid]
    l = low[valid]
    c = close[valid]
    log_hl = np.log(h / l)
    log_co = np.log(c / o)
    term1 = 0.5 * (log_hl ** 2)
    term2 = (2.0 * math.log(2.0) - 1.0) * (log_co ** 2)
    variance = (periods_per_year / len(c)) * np.sum(term1 - term2)
    return round(float(math.sqrt(max(0.0, variance))), 4)


def compute_yang_zhang_volatility(
    open_: np.ndarray,
    high: np.ndarray,
    low: np.ndarray,
    close: np.ndarray,
    periods_per_year: int = 365
) -> float:
    """
    Computes annualized Yang-Zhang Volatility.
    Minimum-variance unbiased estimator incorporating overnight jumps, continuous drift,
    and Rogers-Satchell intraday volatility.
    """
    n = len(close)
    if n < 4:
        return 0.0
    valid = (open_ > 0) & (high > 0) & (low > 0) & (close > 0) & (high >= low)
    if not np.any(valid):
        return 0.0
    o = open_[valid]
    h = high[valid]
    l = low[valid]
    c = close[valid]
    k_bars = len(c)

    # Overnight return: ln(O_i / C_{i-1})
    log_oc = np.log(o[1:] / c[:-1])
    var_o = (periods_per_year / (k_bars - 2)) * np.sum((log_oc - np.mean(log_oc)) ** 2)

    # Open to close return: ln(C_i / O_i)
    log_co = np.log(c[1:] / o[1:])
    var_c = (periods_per_year / (k_bars - 2)) * np.sum((log_co - np.mean(log_co)) ** 2)

    # Rogers-Satchell variance
    log_ho = np.log(h[1:] / o[1:])
    log_lo = np.log(l[1:] / o[1:])
    log_hc = np.log(h[1:] / c[1:])
    log_lc = np.log(l[1:] / c[1:])
    rs = np.sum(log_ho * log_hc + log_lo * log_lc)
    var_rs = (periods_per_year / (k_bars - 1)) * rs

    # Yang-Zhang optimal weighting constant
    k = 0.34 / (1.34 + (k_bars + 1) / (k_bars - 1))
    variance = var_o + k * var_c + (1.0 - k) * var_rs
    return round(float(math.sqrt(max(0.0, variance))), 4)


def compute_volatility_estimates_from_df(
    df: pd.DataFrame,
    symbol: str = "ASSET",
    timeframe: str = "1d",
    periods_per_year: int = 365
) -> VolatilityEstimates:
    """
    Computes all 4 volatility estimators directly from an OHLC DataFrame.
    """
    required = ["open", "high", "low", "close"]
    for col in required:
        if col not in df.columns:
            raise ValueError(f"DataFrame missing required OHLC column: '{col}'")

    o = df["open"].to_numpy(dtype=float)
    h = df["high"].to_numpy(dtype=float)
    l = df["low"].to_numpy(dtype=float)
    c = df["close"].to_numpy(dtype=float)

    # 1. Close-to-close
    returns = np.diff(c) / c[:-1]
    cc_vol = float(np.std(returns, ddof=1) * math.sqrt(periods_per_year)) if len(returns) > 2 else 0.0

    # 2. Parkinson
    p_vol = compute_parkinson_volatility(h, l, periods_per_year)

    # 3. Garman-Klass
    gk_vol = compute_garman_klass_volatility(o, h, l, c, periods_per_year)

    # 4. Yang-Zhang
    yz_vol = compute_yang_zhang_volatility(o, h, l, c, periods_per_year)

    return VolatilityEstimates(
        symbol=symbol,
        timeframe=timeframe,
        sample_bars=len(df),
        close_to_close_vol=round(cc_vol, 4),
        parkinson_vol=p_vol,
        garman_klass_vol=gk_vol,
        yang_zhang_vol=yz_vol,
        timestamp=datetime.datetime.now(datetime.timezone.utc).isoformat()
    )
