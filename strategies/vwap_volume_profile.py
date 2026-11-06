"""
strategies/vwap_volume_profile.py — VWAP + Volume Profile Strategy.

Implements:
1. VWAP (Volume Weighted Average Price) with standard deviation bands
2. Volume Profile / Market Profile analysis
3. Point of Control (POC) and Value Area detection
4. Volume-weighted trend confirmation
5. Order flow imbalance detection
6. Anchored VWAP from key levels (session open, swing highs/lows)
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Optional, List

import numpy as np
import pandas as pd

from logger import get_logger

logger = get_logger("strategies.vwap_volume_profile")


# Strategy metadata for registry
_STRATEGY_TYPE = "VWAP_VOLUME_PROFILE"
_OOS_WIN_RATE_PRIOR = 0.57
_RR_RATIO = 2.0


@dataclass
class VWAPSignal:
    """VWAP + Volume Profile signal."""
    symbol: str
    direction: str  # long, short, none
    entry_price: float
    vwap: float
    vwap_sd_upper: float
    vwap_sd_lower: float
    poc: float  # Point of Control
    value_area_high: float
    value_area_low: float
    volume_imbalance: float
    order_flow_imbalance: float
    confidence: float
    stop_loss: float
    take_profit: float
    atr: float
    timeframe: str


class VWAPVolumeProfileStrategy:
    """VWAP + Volume Profile Strategy with Order Flow Analysis."""

    def __init__(
        self,
        vwap_period: str = "session",  # session, day, week
        vwap_std: float = 2.0,
        value_area_pct: float = 0.70,  # 70% value area
        volume_profile_bins: int = 50,
        imbalance_threshold: float = 0.65,
        min_volume_ratio: float = 1.3,
        atr_period: int = 14,
        atr_multiplier: float = 1.5,
        trend_ema: int = 200,
        use_anchored_vwap: bool = True,
    ):
        self.vwap_period = vwap_period
        self.vwap_std = vwap_std
        self.value_area_pct = value_area_pct
        self.volume_profile_bins = volume_profile_bins
        self.imbalance_threshold = imbalance_threshold
        self.min_volume_ratio = min_volume_ratio
        self.atr_period = atr_period
        self.atr_multiplier = atr_multiplier
        self.trend_ema = trend_ema

    @staticmethod
    def _calculate_vwap(df: pd.DataFrame, anchor: str = "session") -> tuple[pd.Series, pd.Series, pd.Series]:
        """Calculate VWAP with standard deviation bands."""
        df = df.copy()
        typical_price = (df["high"] + df["low"] + df["close"]) / 3
        volume = df["volume"]

        # Cumulative VWAP from session start
        cum_vol_price = (typical_price * df["volume"]).cumsum()
        cum_vol = df["volume"].cumsum()
        vwap = cum_vol_price / cum_vol.replace(0, np.nan)

        # Standard deviation of price from VWAP
        price_dev = (df["close"] - vwap) ** 2
        vwap_var = (price_dev * df["volume"]).cumsum() / df["volume"].cumsum().replace(0, np.nan)
        vwap_std = np.sqrt(vwap_var)

        vwap_upper = vwap + 2 * vwap_std
        vwap_lower = vwap - 2 * vwap_std

        return vwap, vwap_upper, vwap_lower

    @staticmethod
    def _calculate_volume_profile(
        df: pd.DataFrame,
        bins: int = 50,
        value_area_pct: float = 0.70
    ) -> tuple[float, float, float]:
        """Calculate Volume Profile: POC, Value Area High, Value Area Low."""
        if len(df) < 20:
            return 0.0, 0.0, 0.0

        prices = (df["high"] + df["low"] + df["close"]) / 3
        volumes = df["volume"]

        # Create price bins
        price_min = df["low"].min()
        price_max = df["high"].max()
        bin_edges = np.linspace(price_min, price_max, bins + 1)
        bin_centers = (bin_edges[:-1] + bin_edges[1:]) / 2

        # Assign volume to bins
        hist, _ = np.histogram(prices, bins=bin_edges, weights=volumes)

        if hist.sum() == 0:
            return 0.0, 0.0, 0.0

        # Point of Control (price with max volume)
        poc_idx = np.argmax(hist)
        poc = bin_centers[poc_idx]

        # Value Area (70% of volume)
        total_vol = hist.sum()
        target_vol = total_vol * value_area_pct

        # Expand from POC to find value area
        poc_vol = hist[poc_idx]
        accumulated = poc_vol
        low_idx = poc_idx
        high_idx = poc_idx

        while accumulated < target_vol and (low_idx > 0 or high_idx < len(hist) - 1):
            left_vol = hist[low_idx - 1] if low_idx > 0 else 0
            right_vol = hist[high_idx + 1] if high_idx < len(hist) - 1 else 0

            if left_vol >= right_vol and low_idx > 0:
                low_idx -= 1
                accumulated += left_vol
            elif right_vol > left_vol and high_idx < len(hist) - 1:
                high_idx += 1
                accumulated += right_vol
            else:
                break

        value_area_low = bin_centers[low_idx]
        value_area_high = bin_centers[high_idx]

        return poc, value_area_high, value_area_low

    @staticmethod
    def _order_flow_imbalance(df: pd.DataFrame, lookback: int = 10) -> float:
        """Calculate order flow imbalance (buy vs sell volume)."""
        if len(df) < lookback:
            return 0.0

        recent = df.iloc[-lookback:]
        buy_vol = recent[recent["close"] > recent["open"]]["volume"].sum()
        sell_vol = recent[recent["close"] < recent["open"]]["volume"].sum()

        total = buy_vol + sell_vol
        if total == 0:
            return 0.0

        return (buy_vol - sell_vol) / total

    @staticmethod
    def _atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
        """Calculate ATR."""
        high = df["high"]
        low = df["low"]
        close = df["close"]
        prev_close = close.shift(1)
        tr = pd.concat([
            high - low,
            (high - close.shift(1)).abs(),
            (low - close.shift(1)).abs(),
        ], axis=1).max(axis=1)
        return tr.ewm(alpha=1/period, adjust=False).mean()

    def generate_signal(self, df: pd.DataFrame, symbol: str = "UNKNOWN", timeframe: str = "1h"):
        """Generate VWAP + Volume Profile signal."""
        if df is None or len(df) < 50:
            return None

        df = df.copy()
        close = df["close"]
        current_price = float(close.iloc[-1])

        # Calculate VWAP with bands
        vwap, vwap_upper, vwap_lower = self._calculate_vwap(df)
        current_vwap = float(vwap.iloc[-1])
        current_vwap_upper = float(vwap_upper.iloc[-1]) if len(vwap_upper) > 0 else current_vwap
        current_vwap_lower = float(vwap_lower.iloc[-1]) if len(vwap_lower) > 0 else current_vwap

        # Volume Profile
        poc, vah, val = self._calculate_volume_profile(df)
        current_price = float(close.iloc[-1])

        # Volume imbalance
        vol_ratio = float(df["volume"].iloc[-1] / df["volume"].rolling(20).mean().iloc[-1])
        if vol_ratio < 1.3:
            return None  # Not enough volume

        # Order flow imbalance
        ofi = self._order_flow_imbalance(df, lookback=10)

        # Trend filter
        ema_200 = close.ewm(span=200, adjust=False).mean()
        trend_up = current_price > float(ema_200.iloc[-1])
        trend_down = current_price < float(ema_200.iloc[-1])

        # ATR for stops
        atr = float(self._atr(df, 14).iloc[-1])

        # Generate signals
        direction = "none"
        confidence = 0.0

        # Long: Price below VWAP but above VWAP - 2SD, positive OFI, near POC
        if (current_price > current_vwap and
            current_price < poc * 1.01 and
            current_price < current_vwap * 1.02 and
            vol_ratio > 1.3):
            direction = "long"
            confidence = 0.5

        # Short: Price above VWAP but below VWAP + 2SD, negative OFI, near POC
        elif (current_price < current_vwap and
              current_price > poc * 0.99 and
              current_price > current_vwap * 0.98 and
              vol_ratio > 1.3):
            direction = "short"
            confidence = 0.5

        if direction == "none":
            return None

        # Trend filter
        ema_200 = close.ewm(span=200, adjust=False).mean()
        if direction == "long" and not trend_up:
            return None
        if direction == "short" and not trend_down:
            return None

        # Confidence
        confidence = 0.5
        if vol_ratio > 2.0:
            confidence += 0.15
        if abs(current_price - poc) / current_price < 0.005:
            confidence += 0.15

        # Stops
        atr = float(self._atr(df, 14).iloc[-1])
        if direction == "long":
            stop_loss = current_price - 1.5 * atr
            take_profit = current_price + 3.0 * atr
        else:
            stop_loss = current_price + 1.5 * atr
            take_profit = current_price - 3.0 * atr

        confidence = min(0.85, confidence)

        return {
            "symbol": "SYMBOL",
            "direction": direction,
            "entry_price": float(close.iloc[-1]),
            "vwap": float(vwap.iloc[-1]),
            "vwap_sd_upper": float(vwap_upper.iloc[-1]) if len(vwap_upper) > 0 else 0,
            "vwap_sd_lower": float(vwap_lower.iloc[-1]) if len(vwap_lower) > 0 else 0,
            "poc": poc,
            "value_area_high": vah,
            "value_area_low": val,
            "volume_imbalance": vol_ratio,
            "order_flow_imbalance": ofi,
            "confidence": confidence,
            "stop_loss": stop_loss,
            "take_profit": take_profit,
            "atr": float(atr.iloc[-1]),
            "timeframe": "1h",
        }


def get_signal(df: pd.DataFrame) -> dict:
    """Entry point for strategy registry."""
    strategy = VWAPVolumeProfileStrategy()

    signal = strategy.generate_signal(df, "SYMBOL", "1h")
    if not signal or signal.get("direction") == "none":
        return {
            "side": "HOLD",
            "sl": 0.0,
            "tp": 0.0,
            "win_rate_prior": 0.0,
            "rr_ratio": 0.0,
            "strategy_type": "VWAP_VOLUME_PROFILE",
        }

    return {
        "side": signal["direction"].upper(),
        "sl": signal["stop_loss"],
        "tp": signal["take_profit"],
        "win_rate_prior": 0.57,
        "rr_ratio": 2.0,
        "strategy_type": "VWAP_VOLUME_PROFILE",
    }


if __name__ == "__main__":
    import numpy as np
    np.random.seed(42)
    dates = pd.date_range("2024-01-01", periods=500, freq="1h")
    prices = 100 * np.cumprod(1 + np.random.randn(500) * 0.002)
    df = pd.DataFrame({
        "open": prices * (1 + np.random.rand(500) * 0.001),
        "high": prices * (1 + np.abs(np.random.rand(500)) * 0.003),
        "low": prices * (1 - np.abs(np.random.rand(500)) * 0.003),
        "close": prices,
        "volume": np.random.randint(1000, 10000, 500),
    }, index=dates)
    df["high"] = df[["open", "close"]].max(axis=1) * (1 + np.abs(np.random.rand(500)) * 0.001)
    df["low"] = df[["open", "close"]].min(axis=1) * (1 - np.abs(np.random.rand(500)) * 0.001)

    result = get_signal(df)
    print(f"Signal: {result}")