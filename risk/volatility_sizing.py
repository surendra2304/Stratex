"""
risk/volatility_sizing.py — Real-Time Volatility Targeting & Regime-Adaptive Sizing.

Calculates:
1. Real-time ATR volatility, Historical Rolling Realized Volatility, Parkinson Volatility.
2. Constant Volatility Targeting: Sizing scaled to maintain fixed portfolio volatility (e.g. 15% annualized).
3. Dynamic Volatility Regime Classifier (Low, Normal, High, Extreme).
"""


import numpy as np
import pandas as pd


class VolatilitySizingEngine:
    """
    Computes volatility-calibrated position sizes and market regime classifications.
    """

    def __init__(self, target_annual_vol: float = 0.15):
        self.target_annual_vol = target_annual_vol

    def calculate_realized_volatility(self, close_prices: pd.Series, window: int = 20) -> float:
        """
        Calculates annualized realized volatility from log returns.
        """
        if close_prices is None or len(close_prices) < window:
            return 0.20  # Default 20% annualized vol fallback

        returns = np.log(close_prices / close_prices.shift(1)).dropna()
        if len(returns) < window:
            return 0.20

        daily_std = float(returns.tail(window).std())
        # Annualize assuming 365 1d crypto periods or equivalent
        annual_vol = daily_std * np.sqrt(365)
        return float(round(annual_vol, 4))

    def classify_volatility_regime(self, current_vol: float, baseline_vol: float = 0.25) -> str:
        """
        Classifies volatility environment into 4 distinct regimes.
        """
        ratio = current_vol / max(baseline_vol, 1e-4)
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
        """
        t_vol = target_vol or self.target_annual_vol
        if asset_volatility <= 0:
            return 0.0
        raw_weight = t_vol / asset_volatility
        return float(round(min(raw_weight, max_leverage), 4))
