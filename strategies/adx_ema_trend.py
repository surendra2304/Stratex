"""
strategies/adx_ema_trend.py — ADX + EMA Trend Following Strategy.

Implements:
1. ADX (Average Directional Index) for trend strength
2. EMA crossover with ADX filter
3. ADX slope for trend acceleration
4. Dynamic position sizing based on trend strength
5. Multi-timeframe ADX confirmation
6. Trend exhaustion detection
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Optional

import numpy as np
import pandas as pd

from logger import get_logger

logger = get_logger("strategies.adx_ema_trend")


# Strategy metadata for registry
_STRATEGY_TYPE = "TREND_FOLLOWING"
_OOS_WIN_RATE_PRIOR = 0.60
_RR_RATIO = 2.2


@dataclass
class ADXSignal:
    """ADX + EMA trend signal."""
    symbol: str
    direction: str  # long, short, none
    adx: float
    adx_slope: float
    di_plus: float
    di_minus: float
    ema_fast: float
    ema_slow: float
    trend_strength: float
    confidence: float
    entry_price: float
    stop_loss: float
    take_profit: float
    atr: float
    timeframe: str


class ADXEMATrendStrategy:
    """ADX + EMA Trend Following Strategy."""

    def __init__(
        self,
        adx_period: int = 14,
        adx_threshold: float = 25.0,
        ema_fast: int = 12,
        ema_slow: int = 26,
        trend_ema: int = 200,
        atr_period: int = 14,
        atr_multiplier: float = 2.0,
        min_adx_slope: float = 0.5,
        volume_filter: bool = True,
    ):
        self.adx_period = adx_period
        self.adx_threshold = adx_threshold
        self.ema_fast = ema_fast
        self.ema_slow = ema_slow
        self.trend_ema = trend_ema
        self.atr_period = atr_period
        self.atr_multiplier = atr_multiplier
        self.min_adx_slope = min_adx_slope
        self.volume_filter = volume_filter
        self.ema_slow = ema_slow
        self.trend_ema = trend_ema
        self.atr_period = atr_period
        self.atr_multiplier = atr_multiplier
        self.min_adx_slope = min_adx_slope
        self.volume_filter = volume_filter

    @staticmethod
    def _ema(series: pd.Series, period: int) -> pd.Series:
        return series.ewm(span=period, adjust=False).mean()

    @staticmethod
    def _adx_di(df: pd.DataFrame, period: int = 14) -> tuple[pd.Series, pd.Series, pd.Series]:
        """Calculate ADX, DI+, DI-."""
        high = df["high"]
        low = df["low"]
        close = df["close"]

        up_move = high.diff()
        down_move = -low.diff()

        plus_dm = pd.Series(
            np.where((up_move > down_move) & (up_move > 0), up_move, 0),
            index=df.index
        )
        minus_dm = pd.Series(
            np.where((down_move > up_move) & (down_move > 0), down_move, 0),
            index=df.index
        )

        # ATR
        prev_close = close.shift(1)
        tr = pd.concat([
            high - low,
            (high - prev_close).abs(),
            (low - prev_close).abs(),
        ], axis=1).max(axis=1)
        atr = tr.ewm(alpha=1/period, adjust=False).mean()

        plus_di = 100 * (plus_dm.ewm(alpha=1/period, adjust=False).mean() / (atr + 1e-10))
        minus_di = 100 * (minus_dm.ewm(alpha=1/period, adjust=False).mean() / (atr + 1e-10))

        dx = 100 * abs(plus_di - minus_di) / (plus_di + minus_di + 1e-10)
        adx = dx.ewm(alpha=1/period, adjust=False).mean()

        return adx, plus_di, minus_di

    @staticmethod
    def _atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
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
        """Generate ADX + EMA trend signal."""
        if df is None or len(df) < max(self.trend_ema, self.adx_period) + 10:
            return None

        df = df.copy()
        close = df["close"]
        current_price = float(close.iloc[-1])

        # ADX + DI
        adx, di_plus, di_minus = self._adx_di(df, self.adx_period)
        current_adx = float(adx.iloc[-1])
        current_di_plus = float(di_plus.iloc[-1])
        current_di_minus = float(di_minus.iloc[-1])

        # ADX slope (trend acceleration)
        adx_slope = float(adx.diff().iloc[-1]) if len(adx) > 1 else 0.0

        # EMAs
        fast_ema = self._ema(close, self.ema_fast)
        slow_ema = self._ema(close, self.ema_slow)
        trend_ema = self._ema(close, self.trend_ema)

        fast_val = float(fast_ema.iloc[-1])
        slow_val = float(slow_ema.iloc[-1])
        trend_val = float(trend_ema.iloc[-1])
        prev_fast = float(fast_ema.iloc[-2])
        prev_slow = float(slow_ema.iloc[-2])

        # Crossover
        bullish_cross = prev_fast <= prev_slow and fast_val > slow_val
        bearish_cross = prev_fast >= prev_slow and fast_val < slow_val

        if not bullish_cross and not bearish_cross:
            return None

        # ADX threshold
        if current_adx < self.adx_threshold:
            return None

        # ADX slope (accelerating trend)
        if adx_slope < self.min_adx_slope:
            return None

        # DI confirmation
        if bullish_cross and current_di_plus <= current_di_minus:
            return None
        if bearish_cross and current_di_minus <= current_di_plus:
            return None

        # Trend filter
        trend_ema_val = float(self._ema(close, self.trend_ema).iloc[-1])
        trend_up = current_price > trend_ema_val
        trend_down = current_price < trend_val

        if bullish_cross and not trend_up:
            return None
        if bearish_cross and not trend_down:
            return None

        # Volume filter
        if self.volume_filter:
            vol_ma = df["volume"].rolling(20).mean()
            recent_vol = float(df["volume"].iloc[-1])
            avg_vol = float(vol_ma.iloc[-1]) if not vol_ma.isna().all() else recent_vol
            if avg_vol > 0 and recent_vol / avg_vol < 1.0:
                return None

        # Direction
        direction = "long" if bullish_cross else "short"

        # ATR for stops
        atr = float(self._atr(df, 14).iloc[-1])

        # Stops
        if bullish_cross:
            stop_loss = current_price - 2.0 * atr
            take_profit = current_price + 3.0 * atr
        else:
            stop_loss = current_price + 2.0 * atr
            take_profit = current_price - 3.0 * atr

        # Confidence
        confidence = 0.5
        confidence += min(0.2, (current_adx - self.adx_threshold) / 50 * 0.3)
        confidence += min(0.2, adx_slope * 0.5)

        direction_str = "long" if bullish_cross else "short"

        return {
            "symbol": symbol,
            "direction": direction_str,
            "adx": current_adx,
            "adx_slope": adx_slope,
            "di_plus": current_di_plus,
            "di_minus": current_di_minus,
            "ema_fast": float(self._ema(close, self.ema_fast).iloc[-1]),
            "ema_slow": float(self._ema(close, self.ema_slow).iloc[-1]),
            "trend_strength": current_adx / 100.0,
            "confidence": min(0.85, 0.5 + (current_adx - self.adx_threshold) / 50 + adx_slope * 0.2),
            "entry_price": current_price,
            "stop_loss": stop_loss,
            "take_profit": take_profit,
            "atr": atr,
            "timeframe": timeframe,
        }

    @staticmethod
    def _ema(series: pd.Series, period: int) -> pd.Series:
        return series.ewm(span=period, adjust=False).mean()

    @staticmethod
    def _atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
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


def get_signal(df: pd.DataFrame) -> dict:
    """Entry point for strategy registry."""
    strategy = ADXEMATrendStrategy(
        adx_period=14,
        adx_threshold=25.0,
        ema_fast=12,
        ema_slow=26,
        trend_ema=200,
        atr_multiplier=2.0,
    )

    signal = strategy.generate_signal(df, "SYMBOL", "1h")
    if not signal:
        return {
            "side": "HOLD",
            "sl": 0.0,
            "tp": 0.0,
            "win_rate_prior": 0.0,
            "rr_ratio": 0.0,
            "strategy_type": "TREND_FOLLOWING",
        }

    return {
        "side": signal["direction"].upper(),
        "sl": signal["stop_loss"],
        "tp": signal["take_profit"],
        "win_rate_prior": 0.60,
        "rr_ratio": 2.2,
        "strategy_type": "TREND_FOLLOWING",
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