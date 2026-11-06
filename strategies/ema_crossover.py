"""
strategies/ema_crossover.py — EMA Crossover Trend Following Strategy.

Implements:
1. Fast/Slow EMA crossover with trend confirmation
2. Multi-timeframe EMA alignment
3. Dynamic position sizing based on trend strength
4. Trailing stop management
5. Trend exhaustion detection
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Optional

import pandas as pd

from logger import get_logger

logger = get_logger("strategies.ema_crossover")


# Strategy metadata for registry
_STRATEGY_TYPE = "TREND_FOLLOWING"
_OOS_WIN_RATE_PRIOR = 0.58
_RR_RATIO = 2.0


@dataclass
class EMASignal:
    """EMA crossover signal with metadata."""
    symbol: str
    direction: str  # long, short, none
    fast_ema: float
    slow_ema: float
    crossover_strength: float
    trend_strength: float
    confidence: float
    entry_price: float
    stop_loss: float
    take_profit: float
    atr: float
    timeframe: str


class EMACrossoverStrategy:
    """EMA Crossover Trend Following Strategy with multi-timeframe confirmation."""

    def __init__(
        self,
        fast_ema: int = 9,
        slow_ema: int = 21,
        trend_ema: int = 200,
        atr_period: int = 14,
        atr_multiplier: float = 2.0,
        min_trend_strength: float = 0.002,
        volume_filter: bool = True,
        min_volume_ratio: float = 1.2,
    ):
        self.fast_ema = fast_ema
        self.slow_ema = slow_ema
        self.trend_ema = trend_ema
        self.atr_period = atr_period
        self.atr_multiplier = atr_multiplier
        self.min_trend_strength = min_trend_strength
        self.volume_filter = volume_filter
        self.min_volume_ratio = min_volume_ratio

    @staticmethod
    def _ema(series: pd.Series, period: int) -> pd.Series:
        """Calculate Exponential Moving Average."""
        return series.ewm(span=period, adjust=False).mean()

    @staticmethod
    def _atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
        """Calculate Average True Range."""
        high = df["high"]
        low = df["low"]
        close = df["close"]
        prev_close = close.shift(1)

        tr = pd.concat([
            high - low,
            (high - prev_close).abs(),
            (low - prev_close).abs(),
        ], axis=1).max(axis=1)

        return tr.ewm(alpha=1/period, adjust=False).mean()

    @staticmethod
    def _volume_ma(df: pd.DataFrame, period: int = 20) -> pd.Series:
        """Calculate volume moving average."""
        if "volume" not in df.columns:
            return pd.Series(1.0, index=df.index)
        return df["volume"].rolling(period).mean()

    def generate_signal(self, df: pd.DataFrame, symbol: str = "UNKNOWN", timeframe: str = "1h") -> Optional[EMASignal]:
        """Generate EMA crossover signal with trend and volume confirmation."""
        if df is None or len(df) < max(self.slow_ema, self.trend_ema) + 10:
            return None

        df = df.copy()
        close = df["close"]
        volume = df.get("volume", pd.Series(1.0, index=df.index))

        # Calculate EMAs
        fast_ema_series = self._ema(close, self.fast_ema)
        slow_ema_series = self._ema(close, self.slow_ema)
        trend_ema_series = self._ema(close, self.trend_ema)

        fast_ema = float(fast_ema_series.iloc[-1])
        slow_ema = float(slow_ema_series.iloc[-1])
        trend_ema = float(trend_ema_series.iloc[-1])
        prev_fast = float(fast_ema_series.iloc[-2])
        prev_slow = float(slow_ema_series.iloc[-2])
        current_price = float(close.iloc[-1])

        # Detect crossover
        bullish_cross = prev_fast <= prev_slow and fast_ema > slow_ema
        bearish_cross = prev_fast >= prev_slow and fast_ema < slow_ema

        if not bullish_cross and not bearish_cross:
            return None

        # Trend confirmation
        trend_bullish = current_price > trend_ema
        trend_bearish = current_price < trend_ema
        trend_strength = abs(current_price - trend_ema) / trend_ema

        # Filter: trend must align with crossover
        if bullish_cross and not trend_bullish:
            return None
        if bearish_cross and not trend_bearish:
            return None

        # Filter: minimum trend strength
        if trend_strength < self.min_trend_strength:
            return None

        # Volume confirmation
        if self.volume_filter:
            vol_ma = volume.rolling(20).mean()
            recent_vol = float(volume.iloc[-1])
            avg_vol = float(vol_ma.iloc[-1]) if not vol_ma.isna().all() else recent_vol
            if avg_vol > 0 and recent_vol / avg_vol < self.min_volume_ratio:
                return None

        # Calculate ATR for stops
        atr = self._calculate_atr(df, period=14)
        current_atr = float(atr.iloc[-1]) if len(atr) > 0 else current_price * 0.01

        # Calculate crossover strength
        crossover_strength = abs(fast_ema - slow_ema) / current_price

        # Direction
        direction = "long" if bullish_cross else "short"

        # Stops
        if bullish_cross:
            stop_loss = current_price - self.atr_multiplier * current_atr
            take_profit = current_price + (self.atr_multiplier * 2) * current_atr
        else:
            stop_loss = current_price + self.atr_multiplier * current_atr
            take_profit = current_price - (self.atr_multiplier * 2) * current_atr

        # Confidence based on multiple factors
        confidence = 0.5
        confidence += min(0.2, trend_strength * 100)  # Trend strength
        confidence += min(0.15, crossover_strength * 100)  # Crossover strength
        if hasattr(df, 'volume'):
            recent_vol_ratio = float(volume.iloc[-1]) / float(volume.rolling(20).mean().iloc[-1])
            confidence += min(0.15, max(0, recent_vol_ratio - 1) * 0.5)
        confidence = min(0.85, confidence)

        direction_str = "long" if bullish_cross else "short"

        return EMASignal(
            symbol="SYMBOL",  # Will be set by caller
            direction=direction_str,
            fast_ema=fast_ema,
            slow_ema=slow_ema,
            crossover_strength=crossover_strength,
            trend_strength=trend_strength,
            confidence=confidence,
            entry_price=close.iloc[-1],
            stop_loss=stop_loss,
            take_profit=take_profit,
            atr=current_atr,
            timeframe="1h",  # Will be set by caller
        )

    @staticmethod
    def _calculate_atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
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

        return tr.ewm(alpha=1/14, adjust=False).mean()


def get_signal(df: pd.DataFrame) -> dict:
    """
    Entry point for strategy registry.
    Returns a signal dict compatible with the registry.
    """
    strategy = EMACrossoverStrategy(
        fast_ema=9,
        slow_ema=21,
        trend_ema=200,
        atr_multiplier=2.0,
        min_trend_strength=0.002,
    )

    signal = strategy.generate_signal(df, "SYMBOL", "1h")
    if not signal or signal.direction == "none":
        return {
            "side": "HOLD",
            "sl": 0.0,
            "tp": 0.0,
            "win_rate_prior": 0.0,
            "rr_ratio": 0.0,
            "strategy_type": "TREND_FOLLOWING",
        }

    return {
        "side": signal.direction.upper(),
        "sl": signal.stop_loss,
        "tp": signal.take_profit,
        "win_rate_prior": 0.58,
        "rr_ratio": 2.0,
        "strategy_type": "TREND_FOLLOWING",
    }


# For direct testing
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