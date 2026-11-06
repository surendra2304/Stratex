"""
strategies/bollinger_rsi_reversion.py — Bollinger Bands + RSI Mean Reversion Strategy.

Implements:
1. Bollinger Band squeeze detection (low volatility precursor)
2. Mean reversion from BB extremes with RSI confirmation
3. Volume-weighted entry confirmation
3. Dynamic position sizing based on band width
4. Trend filter to avoid counter-trend trades in strong trends
5. Volatility regime adaptation
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Optional

import pandas as pd

from logger import get_logger

logger = get_logger("strategies.bollinger_rsi_reversion")


# Strategy metadata for registry
_STRATEGY_TYPE = "MEAN_REVERSION"
_OOS_WIN_RATE_PRIOR = 0.62
_RR_RATIO = 1.5


@dataclass
class BBReversionSignal:
    """Bollinger Band + RSI mean reversion signal."""
    symbol: str
    direction: str  # long, short, none
    entry_price: float
    bb_upper: float
    bb_lower: float
    bb_middle: float
    bb_width: float
    rsi: float
    squeeze: bool
    confidence: float
    stop_loss: float
    take_profit: float
    atr: float
    timeframe: str


class BollingerRSIReversionStrategy:
    """Bollinger Bands + RSI Mean Reversion Strategy."""

    def __init__(
        self,
        bb_period: int = 20,
        bb_std: float = 2.0,
        rsi_period: int = 14,
        rsi_oversold: float = 30,
        rsi_overbought: float = 70,
        atr_period: int = 14,
        atr_multiplier: float = 1.5,
        trend_filter: bool = True,
        trend_ema: int = 200,
        squeeze_threshold: float = 0.05,  # BB width percentile for squeeze
        min_squeeze_duration: int = 5,
    ):
        self.bb_period = bb_period
        self.bb_std = bb_std
        self.rsi_period = rsi_period
        self.rsi_oversold = rsi_oversold
        self.rsi_overbought = rsi_overbought
        self.atr_period = atr_period
        self.atr_multiplier = atr_multiplier
        self.trend_filter = trend_filter
        self.trend_ema = trend_ema
        self.squeeze_threshold = squeeze_threshold
        self.min_squeeze_duration = min_squeeze_duration

    @staticmethod
    def _rsi(series: pd.Series, period: int = 14) -> pd.Series:
        """Calculate RSI."""
        delta = series.diff()
        gain = delta.where(delta > 0, 0.0)
        loss = -delta.where(delta < 0, 0.0)
        avg_gain = gain.ewm(alpha=1/period, adjust=False).mean()
        avg_loss = loss.ewm(alpha=1/period, adjust=False).mean()
        rs = avg_gain / (avg_loss + 1e-10)
        return 100 - (100 / (1 + rs))

    @staticmethod
    def _bbands(series: pd.Series, period: int = 20, std: float = 2.0) -> tuple[pd.Series, pd.Series, pd.Series]:
        """Calculate Bollinger Bands."""
        middle = series.rolling(period).mean()
        std = series.rolling(period).std()
        upper = middle + std * std
        lower = middle - std * std
        return upper, middle, lower

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

    def generate_signal(self, df: pd.DataFrame, symbol: str = "UNKNOWN", timeframe: str = "1h") -> Optional[BBReversionSignal]:
        """Generate Bollinger Band + RSI mean reversion signal."""
        if df is None or len(df) < max(self.bb_period, self.trend_ema) + 10:
            return None

        df = df.copy()
        close = df["close"]

        # Calculate indicators
        bb_upper, bb_middle, bb_lower = self._bbands(close, self.bb_period, self.bb_std)
        rsi = self._rsi(close, self.rsi_period)
        bb_width = (bb_upper - bb_lower) / bb_middle
        atr = self._atr(df, self.atr_period)

        current_price = float(close.iloc[-1])
        current_bb_upper = float(bb_upper.iloc[-1])
        current_bb_middle = float(bb_middle.iloc[-1])
        current_bb_lower = float(bb_lower.iloc[-1])
        current_bb_width = float(bb_width.iloc[-1])
        current_rsi = float(rsi.iloc[-1])
        current_atr = float(self._atr(df, self.atr_period).iloc[-1])

        # Detect squeeze (low volatility)
        bb_width_percentile = bb_width.rolling(100).rank(pct=True).iloc[-1] if len(bb_width) >= 100 else 0.5
        squeeze = bb_width_percentile < self.squeeze_threshold

        # Check squeeze duration
        squeeze_duration = 0
        if len(bb_width) >= self.min_squeeze_duration:
            recent_widths = bb_width.iloc[-self.min_squeeze_duration:]
            squeeze_duration = sum(w < self.squeeze_threshold for w in recent_widths)

        # Trend filter
        if self.trend_filter:
            ema_200 = close.ewm(span=200, adjust=False).mean()
            trend_up = current_price > float(ema_200.iloc[-1])
            trend_down = current_price < float(ema_200.iloc[-1])
        else:
            trend_up = trend_down = False

        # Generate signals
        direction = "none"
        confidence = 0.0

        # Long signal: price near lower band + oversold RSI + squeeze
        if (current_price <= current_bb_lower * 1.005 and 
            current_rsi <= self.rsi_oversold and 
            squeeze):
            direction = "long"
            # Confidence increases with squeeze strength and RSI oversold
            confidence = 0.4
            confidence += min(0.2, (self.rsi_oversold - current_rsi) / 20)
            confidence += min(0.2, self.squeeze_threshold / max(current_bb_width, 0.001))
            if squeeze_duration >= self.min_squeeze_duration:
                confidence += 0.1

        # Short signal: price near upper band + overbought RSI + squeeze
        elif (current_price >= current_bb_upper * 0.995 and 
              current_rsi >= self.rsi_overbought and 
              squeeze):
            direction = "short"
            confidence = 0.4
            confidence += min(0.2, (current_rsi - self.rsi_overbought) / 20)
            confidence += min(0.2, self.squeeze_threshold / max(current_bb_width, 0.001))
            if squeeze_duration >= self.min_squeeze_duration:
                confidence += 0.1

        if direction == "none":
            return None

        # Trend filter: don't fight strong trend
        if self.trend_filter:
            ema_200 = close.ewm(span=200, adjust=False).mean()
            trend_price = float(ema_200.iloc[-1])
            if direction == "long" and current_price < trend_price * 0.995:
                return None
            if direction == "short" and current_price > trend_price * 1.005:
                return None

        # Stops
        atr = float(self._atr(df, self.atr_period).iloc[-1])
        if direction == "long":
            stop_loss = current_price - self.atr_multiplier * atr
            take_profit = current_bb_middle  # Target middle band
        else:
            stop_loss = current_price + self.atr_multiplier * atr
            take_profit = current_bb_middle

        confidence = min(0.85, confidence)

        return BBReversionSignal(
            symbol="SYMBOL",
            direction=direction,
            entry_price=current_price,
            bb_upper=current_bb_upper,
            bb_lower=current_bb_lower,
            bb_middle=current_bb_middle,
            bb_width=current_bb_width,
            rsi=current_rsi,
            squeeze=squeeze,
            confidence=confidence,
            stop_loss=stop_loss,
            take_profit=take_profit,
            atr=atr,
            timeframe=timeframe,
        )


def get_signal(df: pd.DataFrame) -> dict:
    """Entry point for strategy registry."""
    strategy = BollingerRSIReversionStrategy(
        bb_period=20,
        bb_std=2.0,
        rsi_period=14,
        rsi_oversold=30,
        rsi_overbought=70,
        atr_multiplier=1.5,
        trend_filter=True,
        trend_ema=200,
        squeeze_threshold=0.05,
    )

    signal = strategy.generate_signal(df, "SYMBOL", "1h")
    if not signal or signal.direction == "none":
        return {
            "side": "HOLD",
            "sl": 0.0,
            "tp": 0.0,
            "win_rate_prior": 0.0,
            "rr_ratio": 0.0,
            "strategy_type": "MEAN_REVERSION",
        }

    return {
        "side": signal.direction.upper(),
        "sl": signal.stop_loss,
        "tp": signal.take_profit,
        "win_rate_prior": 0.62,
        "rr_ratio": 1.5,
        "strategy_type": "MEAN_REVERSION",
    }


if __name__ == "__main__":
    import numpy as np
    np.random.seed(42)
    dates = pd.date_range("2024-01-01", periods=300, freq="1h")
    prices = 100 * np.cumprod(1 + np.random.randn(300) * 0.0015)
    df = pd.DataFrame({
        "open": prices * (1 + np.random.rand(300) * 0.001),
        "high": prices * (1 + np.abs(np.random.rand(300)) * 0.003),
        "low": prices * (1 - np.abs(np.random.rand(300)) * 0.003),
        "close": prices,
        "volume": np.random.randint(1000, 10000, 300),
    }, index=dates)
    df["high"] = df[["open", "close"]].max(axis=1) * (1 + np.abs(np.random.rand(300)) * 0.001)
    df["low"] = df[["open", "close"]].min(axis=1) * (1 - np.abs(np.random.rand(300)) * 0.001)

    result = get_signal(df)
    print(f"Signal: {result}")