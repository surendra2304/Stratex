"""
strategies/regime_detector.py — Advanced Market Regime Detection Engine.

Detects market regimes using multiple methods:
1. Volatility-based regime detection (ATR, realized volatility)
2. Trend-based regime detection (EMA alignment, ADX)
3. Momentum-based regime detection (ROC, RSI)
4. Volume-based regime detection (relative volume, volume trends)
5. Composite regime scoring with confidence
6. Regime transition detection and prediction
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

import numpy as np
import pandas as pd

from logger import get_logger

logger = get_logger("strategies.regime")


class MarketRegime(str, Enum):
    TRENDING_UP = "TRENDING_UP"
    TRENDING_DOWN = "TRENDING_DOWN"
    RANGING = "RANGING"
    HIGH_VOLATILITY = "HIGH_VOLATILITY"
    LOW_VOLATILITY = "LOW_VOLATILITY"
    CHOPPY = "CHOPPY"
    BREAKOUT = "BREAKOUT"
    REVERSAL = "REVERSAL"
    UNKNOWN = "UNKNOWN"


@dataclass
class RegimeSignal:
    """A market regime signal with confidence."""
    regime: MarketRegime
    confidence: float
    indicators: dict[str, float] = field(default_factory=dict)
    detected_at: float = 0.0
    previous_regime: str = ""
    transition: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "regime": self.regime.value,
            "confidence": self.confidence,
            "indicators": self.indicators,
            "detected_at": self.detected_at,
            "previous_regime": self.previous_regime,
            "transition": self.transition,
        }


class RegimeDetector:
    """Advanced market regime detection engine."""

    def __init__(self):
        self.regime_history: list[RegimeSignal] = []
        self.current_regime: MarketRegime = MarketRegime.UNKNOWN

    def detect_regime(self, df: pd.DataFrame) -> RegimeSignal:
        """Detect the current market regime from price data."""
        if df is None or len(df) < 50:
            return RegimeSignal(regime=MarketRegime.UNKNOWN, confidence=0.0)

        # Ensure features are present
        df = self._add_features(df)

        # Run all detection methods
        volatility_signal = self._detect_volatility_regime(df)
        trend_signal = self._detect_trend_regime(df)
        momentum_signal = self._detect_momentum_regime(df)
        volume_signal = self._detect_volume_regime(df)

        # Combine signals into composite regime
        composite = self._combine_signals(
            volatility_signal, trend_signal, momentum_signal, volume_signal
        )

        # Track regime transitions
        previous = self.current_regime
        composite.previous_regime = previous.value
        composite.transition = composite.regime != previous

        if composite.transition:
            logger.info(f"Regime transition: {previous.value} -> {composite.regime.value}")

        self.current_regime = composite.regime
        self.regime_history.append(composite)

        return composite

    def _add_features(self, df: pd.DataFrame) -> pd.DataFrame:
        """Add required features for regime detection."""
        df = df.copy()
        close = df["close"]
        high = df["high"]
        low = df["low"]
        vol = df.get("volume", pd.Series(1.0, index=df.index))

        # EMAs
        df["ema_20"] = close.ewm(span=20, adjust=False).mean()
        df["ema_50"] = close.ewm(span=50, adjust=False).mean()
        df["ema_200"] = close.ewm(span=200, adjust=False).mean()

        # ATR
        prev_close = close.shift(1)
        tr = pd.concat([
            high - low,
            (high - prev_close).abs(),
            (low - prev_close).abs(),
        ], axis=1).max(axis=1)
        df["atr_14"] = tr.ewm(alpha=1/14, adjust=False).mean()

        # ADX
        df["adx_14"] = self._compute_adx(df, 14)

        # RSI
        df["rsi_14"] = self._compute_rsi(close, 14)

        # Realized volatility
        df["realized_vol"] = close.pct_change().rolling(20).std() * math.sqrt(252)

        # Relative volume
        df["volume_ma"] = vol.rolling(20).mean()
        df["rel_volume"] = vol / df["volume_ma"]

        # Rate of change
        df["roc_10"] = close.pct_change(10) * 100

        # Bollinger Band width
        bb_mid = close.rolling(20).mean()
        bb_std = close.rolling(20).std()
        df["bb_width"] = (bb_std * 2) / bb_mid * 100

        return df

    def _compute_rsi(self, close: pd.Series, period: int = 14) -> pd.Series:
        delta = close.diff()
        gain = delta.where(delta > 0, 0.0)
        loss = -delta.where(delta < 0, 0.0)
        avg_gain = gain.ewm(alpha=1/period, adjust=False).mean()
        avg_loss = loss.ewm(alpha=1/period, adjust=False).mean()
        rs = avg_gain / (avg_loss + 1e-10)
        return 100 - (100 / (1 + rs))

    def _compute_adx(self, df: pd.DataFrame, period: int = 14) -> pd.Series:
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

        atr = df["atr_14"]
        plus_di = 100 * (plus_dm.ewm(alpha=1/period, adjust=False).mean() / (atr + 1e-10))
        minus_di = 100 * (minus_dm.ewm(alpha=1/period, adjust=False).mean() / (atr + 1e-10))

        dx = 100 * (abs(plus_di - minus_di) / (plus_di + minus_di + 1e-10))
        return dx.ewm(alpha=1/period, adjust=False).mean()

    def _detect_volatility_regime(self, df: pd.DataFrame) -> RegimeSignal:
        """Detect regime based on volatility levels."""
        last = df.iloc[-1]
        realized_vol = float(last.get("realized_vol", 0.15))
        atr = float(last.get("atr_14", 0.0))
        bb_width = float(last.get("bb_width", 2.0))

        # Classify based on volatility
        if realized_vol > 0.40 or bb_width > 8.0:
            regime = MarketRegime.HIGH_VOLATILITY
            confidence = min(1.0, realized_vol / 0.50)
        elif realized_vol < 0.08 or bb_width < 1.5:
            regime = MarketRegime.LOW_VOLATILITY
            confidence = min(1.0, (0.08 - realized_vol) / 0.08 + 0.5)
        else:
            regime = MarketRegime.UNKNOWN
            confidence = 0.5

        return RegimeSignal(
            regime=regime,
            confidence=confidence,
            indicators={
                "realized_vol": realized_vol,
                "atr": atr,
                "bb_width": bb_width,
            },
            detected_at=pd.Timestamp.now().timestamp(),
        )

    def _detect_trend_regime(self, df: pd.DataFrame) -> RegimeSignal:
        """Detect regime based on trend strength."""
        last = df.iloc[-1]
        ema_20 = float(last.get("ema_20", 0))
        ema_50 = float(last.get("ema_50", 0))
        ema_200 = float(last.get("ema_200", 0))
        adx = float(last.get("adx_14", 0))
        close = float(last["close"])

        # Strong trend: ADX > 25 and EMAs aligned
        if adx > 25:
            if close > ema_20 > ema_50 > ema_200:
                regime = MarketRegime.TRENDING_UP
                confidence = min(1.0, adx / 40)
            elif close < ema_20 < ema_50 < ema_200:
                regime = MarketRegime.TRENDING_DOWN
                confidence = min(1.0, adx / 40)
            else:
                regime = MarketRegime.RANGING
                confidence = 0.5
        else:
            # Weak trend or ranging
            regime = MarketRegime.RANGING
            confidence = min(1.0, (25 - adx) / 25 + 0.5)

        return RegimeSignal(
            regime=regime,
            confidence=confidence,
            indicators={
                "adx": adx,
                "ema_alignment": abs(ema_20 - ema_50) / close,
            },
            detected_at=pd.Timestamp.now().timestamp(),
        )

    def _detect_momentum_regime(self, df: pd.DataFrame) -> RegimeSignal:
        """Detect regime based on momentum indicators."""
        last = df.iloc[-1]
        rsi = float(last.get("rsi_14", 50))
        roc = float(last.get("roc_10", 0))

        # Strong momentum
        if roc > 5 and rsi > 60:
            regime = MarketRegime.TRENDING_UP
            confidence = min(1.0, (roc - 5) / 10 + 0.5)
        elif roc < -5 and rsi < 40:
            regime = MarketRegime.TRENDING_DOWN
            confidence = min(1.0, (abs(roc) - 5) / 10 + 0.5)
        elif 40 <= rsi <= 60 and abs(roc) < 3:
            regime = MarketRegime.RANGING
            confidence = 0.7
        else:
            regime = MarketRegime.CHOPPY
            confidence = 0.5

        return RegimeSignal(
            regime=regime,
            confidence=confidence,
            indicators={"rsi": rsi, "roc": roc},
            detected_at=pd.Timestamp.now().timestamp(),
        )

    def _detect_volume_regime(self, df: pd.DataFrame) -> RegimeSignal:
        """Detect regime based on volume patterns."""
        last = df.iloc[-1]
        rel_vol = float(last.get("rel_volume", 1.0))

        # High volume breakout
        if rel_vol > 2.0:
            regime = MarketRegime.BREAKOUT
            confidence = min(1.0, (rel_vol - 2.0) / 2.0 + 0.5)
        elif rel_vol < 0.7:
            regime = MarketRegime.LOW_VOLATILITY
            confidence = min(1.0, (0.7 - rel_vol) / 0.7 + 0.5)
        else:
            regime = MarketRegime.UNKNOWN
            confidence = 0.5

        return RegimeSignal(
            regime=regime,
            confidence=confidence,
            indicators={"rel_volume": rel_vol},
            detected_at=pd.Timestamp.now().timestamp(),
        )

    def _combine_signals(
        self,
        volatility: RegimeSignal,
        trend: RegimeSignal,
        momentum: RegimeSignal,
        volume: RegimeSignal,
    ) -> RegimeSignal:
        """Combine multiple regime signals into a composite regime."""
        signals = [volatility, trend, momentum, volume]

        # Count votes for each regime
        regime_votes: dict[MarketRegime, list[RegimeSignal]] = {}

        for signal in signals:
            if signal.regime != MarketRegime.UNKNOWN:
                regime_votes.setdefault(signal.regime, []).append(signal)

        # Find the most voted regime
        if not regime_votes:
            return RegimeSignal(regime=MarketRegime.UNKNOWN, confidence=0.0)

        # Rank by number of votes, then by average confidence
        ranked = sorted(
            regime_votes.items(),
            key=lambda x: (len(x[1]), sum(s.confidence for s in x[1]) / len(x[1])),
            reverse=True,
        )

        best_regime, best_signals = ranked[0]

        # Calculate composite confidence
        avg_confidence = sum(s.confidence for s in best_signals) / len(best_signals)
        vote_ratio = len(best_signals) / len(signals)
        composite_confidence = avg_confidence * vote_ratio

        # Collect all indicators
        all_indicators = {}
        for signal in signals:
            all_indicators.update(signal.indicators)

        return RegimeSignal(
            regime=best_regime,
            confidence=composite_confidence,
            indicators=all_indicators,
            detected_at=pd.Timestamp.now().timestamp(),
        )

    def get_regime_history(self, limit: int = 20) -> list[dict]:
        """Get recent regime history."""
        return [s.to_dict() for s in self.regime_history[-limit:]]

    def get_current_regime(self) -> str:
        """Get the current market regime."""
        return self.current_regime.value

    def predict_next_regime(self, df: pd.DataFrame) -> str:
        """Predict the next likely regime based on trends."""
        if len(self.regime_history) < 3:
            return self.current_regime.value

        # Look at recent regime transitions
        recent = self.regime_history[-5:]

        # Count regime frequency
        regime_counts: dict[str, int] = {}
        for signal in recent:
            regime_counts[signal.regime.value] = regime_counts.get(signal.regime.value, 0) + 1

        # Return most frequent recent regime (momentum continuation)
        if regime_counts:
            most_common = max(regime_counts, key=regime_counts.get)
            return most_common

        return self.current_regime.value


# Module-level singleton
regime_detector = RegimeDetector()
