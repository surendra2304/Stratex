"""
risk/volatility_sizing.py — Real-Time Volatility Targeting & Regime-Adaptive Sizing.

Calculates:
1. Real-time ATR volatility, Historical Rolling Realized Volatility, Parkinson Volatility.
2. Constant Volatility Targeting: Sizing scaled to maintain fixed portfolio volatility (e.g. 15% annualized).
3. Dynamic Volatility Regime Classifier (Low, Normal, High, Extreme).

Invalid or unmeasurable inputs never masquerade as measurements: realized
volatility is ``nan`` when it cannot be computed (it used to be a hard-coded
0.20 "fallback"), an unmeasurable regime classifies as the most restrictive
one, and a NaN/zero volatility yields a 0.0 weight instead of NaN.
"""

import math

import numpy as np
import pandas as pd

from numeric_safety import finite_float, positive_float


class VolatilitySizingEngine:
    """
    Computes volatility-calibrated position sizes and market regime classifications.
    """

    def __init__(self, target_annual_vol: float = 0.15):
        target = positive_float(target_annual_vol)
        if target is None:
            raise ValueError(f"target_annual_vol must be finite and > 0, got {target_annual_vol!r}")
        self.target_annual_vol = target

    def calculate_realized_volatility(self, close_prices: pd.Series, window: int = 20) -> float:
        """
        Calculates annualized realized volatility from log returns.

        Returns ``nan`` when volatility cannot be measured: fewer than
        ``window + 1`` prices, or a non-positive/non-finite price inside the
        window (``log`` of those produced ``-inf``/NaN returns).
        """
        if close_prices is None or window < 2 or len(close_prices) < window + 1:
            return math.nan
        prices = pd.to_numeric(pd.Series(close_prices), errors="coerce").tail(window + 1)
        if not bool(np.isfinite(prices).all()) or not bool((prices > 0).all()):
            return math.nan

        returns = np.log(prices / prices.shift(1)).dropna()
        if len(returns) < window:
            return math.nan

        daily_std = float(returns.std())
        # Annualize assuming 365 1d crypto periods or equivalent
        annual_vol = daily_std * np.sqrt(365)
        return float(round(annual_vol, 4)) if math.isfinite(annual_vol) else math.nan

    def classify_volatility_regime(self, current_vol: float, baseline_vol: float = 0.25) -> str:
        """
        Classifies volatility environment into 4 distinct regimes.

        Unmeasurable (NaN/negative) volatility or an invalid baseline is
        classified as EXTREME_VOLATILITY, the most restrictive regime.
        """
        current = finite_float(current_vol)
        baseline = positive_float(baseline_vol)
        if current is None or current < 0 or baseline is None:
            return "EXTREME_VOLATILITY"
        ratio = current / baseline
        if ratio < 0.70:
            return "LOW_VOLATILITY"
        elif ratio <= 1.30:
            return "NORMAL_VOLATILITY"
        elif ratio <= 2.00:
            return "HIGH_VOLATILITY"
        else:
            return "EXTREME_VOLATILITY"

    def compute_vol_targeted_weight(
        self,
        asset_volatility: float,
        target_vol: float | None = None,
        max_leverage: float = 1.0
    ) -> float:
        """
        Weight = Target Volatility / Asset Volatility (clamped by max leverage).

        Returns 0.0 for any invalid input (NaN/zero/negative volatility, target
        or leverage); the old code returned NaN for a NaN volatility.
        """
        t_vol = self.target_annual_vol if target_vol is None else positive_float(target_vol)
        asset_vol = positive_float(asset_volatility)
        leverage = finite_float(max_leverage)
        if t_vol is None or asset_vol is None or leverage is None or leverage <= 0:
            return 0.0
        raw_weight = t_vol / asset_vol
        return float(round(min(raw_weight, leverage), 4))
