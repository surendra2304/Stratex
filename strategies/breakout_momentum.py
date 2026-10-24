"""
Stratex/strategies/breakout_momentum.py — Breakout & Momentum Strategy.

Implements:
1. Volatility Breakout: Donchian channel breakout with volatility filter
2. Relative Momentum: cross-asset momentum ranking
3. Volume Confirmation: volume-weighted signal confirmation
4. Trend Filter: EMA-based trend direction filter
5. Momentum Reversal: detect momentum exhaustion and reversals
6. Multi-Timeframe Confirmation: multi-TF signal alignment
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any


@dataclass
class BreakoutSignal:
    """A breakout trading signal."""
    symbol: str
    direction: str  # long, short, none
    entry_price: float
    breakout_level: float
    volatility: float
    volume_confirmation: bool
    confidence: float
    stop_loss: float
    target: float


@dataclass
class MomentumScore:
    """Momentum score for a symbol."""
    symbol: str
    score: float
    trend: str  # bullish, bearish, neutral
    components: dict[str, float] = field(default_factory=dict)
    relative_strength: float = 0.0


@dataclass
class ReversalSignal:
    """A momentum reversal signal."""
    symbol: str
    direction: str  # bullish_reversal, bearish_reversal
    current_trend: str
    reversal_strength: float
    exhaustion_indicators: list[str]
    confidence: float


class BreakoutMomentumStrategy:
    """Breakout and momentum strategy with multi-confirmation."""

    def __init__(
        self,
        lookback: int = 20,
        breakout_multiplier: float = 1.0,
        volume_threshold: float = 1.5,
        trend_ema: int = 200,
    ):
        self.lookback = lookback
        self.breakout_multiplier = breakout_multiplier
        self.volume_threshold = volume_threshold
        self.trend_ema = trend_ema
        self._momentum_scores: dict[str, MomentumScore] = {}

    # ── Volatility Breakout ─────────────────────────────────────────────────

    def calculate_donchian_channel(
        self,
        prices: list[float],
        lookback: int | None = None,
    ) -> tuple[float, float, float]:
        """Calculate Donchian channel (upper, lower, middle)."""
        lookback = lookback or self.lookback
        if len(prices) < lookback:
            return (0.0, 0.0, 0.0)

        recent = prices[-lookback:]
        upper = max(recent)
        lower = min(recent)
        middle = (upper + lower) / 2.0

        return (upper, lower, middle)

    def calculate_atr(
        self,
        prices: list[float],
        period: int = 14,
    ) -> float:
        """Calculate Average True Range."""
        if len(prices) < period + 1:
            return 0.0

        true_ranges = []
        for i in range(1, len(prices)):
            tr = max(
                prices[i] - prices[i-1],
                abs(prices[i] - prices[i-1]),
            )
            true_ranges.append(tr)

        recent_tr = true_ranges[-period:]
        return sum(recent_tr) / len(recent_tr) if recent_tr else 0.0

    def generate_breakout_signal(
        self,
        symbol: str,
        prices: list[float],
        volumes: list[float] | None = None,
    ) -> BreakoutSignal | None:
        """Generate a breakout signal."""
        if len(prices) < self.lookback + 1:
            return None

        upper, lower, middle = self.calculate_donchian_channel(prices)
        current_price = prices[-1]
        prev_price = prices[-2]

        # Breakout detection
        direction = "none"
        breakout_level = 0.0

        if current_price > upper * self.breakout_multiplier and prev_price <= upper * self.breakout_multiplier:
            direction = "long"
            breakout_level = upper * self.breakout_multiplier
        elif current_price < lower * self.breakout_multiplier and prev_price >= lower * self.breakout_multiplier:
            direction = "short"
            breakout_level = lower * self.breakout_multiplier

        if direction == "none":
            return BreakoutSignal(
                symbol=symbol,
                direction="none",
                entry_price=current_price,
                breakout_level=breakout_level,
                volatility=self.calculate_atr(prices),
                volume_confirmation=False,
                confidence=0.0,
                stop_loss=0.0,
                target=0.0,
            )

        # Volatility filter
        atr = self.calculate_atr(prices)
        if atr > 0 and atr / current_price > 0.05:
            return None  # Too volatile

        # Volume confirmation
        volume_confirmation = False
        if volumes and len(volumes) > 0:
            avg_volume = sum(volumes[-20:]) / min(20, len(volumes))
            recent_volume = volumes[-1] if volumes else 0
            volume_confirmation = recent_volume > avg_volume * self.volume_threshold

        # Calculate stop loss and target
        if direction == "long":
            stop_loss = lower
            target = current_price + (current_price - stop_loss) * 2.0
        else:
            stop_loss = upper
            target = current_price - (stop_loss - current_price) * 2.0

        # Confidence
        confidence = 0.5
        if volume_confirmation:
            confidence += 0.2
        if atr / current_price < 0.02:
            confidence += 0.1

        confidence = min(1.0, confidence)

        return BreakoutSignal(
            symbol=symbol,
            direction=direction,
            entry_price=current_price,
            breakout_level=breakout_level,
            volatility=atr,
            volume_confirmation=volume_confirmation,
            confidence=confidence,
            stop_loss=stop_loss,
            target=target,
        )

    # ── Relative Momentum ───────────────────────────────────────────────────

    def calculate_momentum_score(
        self,
        symbol: str,
        prices: list[float],
        lookback: int = 20,
    ) -> MomentumScore:
        """Calculate momentum score for a symbol."""
        if len(prices) < lookback + 1:
            return MomentumScore(symbol=symbol, score=0.0, trend="neutral")

        current = prices[-1]
        past = prices[-1 - lookback]

        # Price return
        return_pct = (current - past) / past

        # ROC (Rate of Change)
        roc = (current - past) / past * 100.0

        # RSI
        rsi = self._calculate_rsi(prices, 14)

        # EMA trend
        ema = self._calculate_ema(prices, self.trend_ema)
        trend = "bullish" if current > ema else "bearish" if current < ema else "neutral"

        # Combine into score
        score = 0.0
        score += max(-1.0, min(1.0, return_pct * 10.0)) * 0.4  # 40% return
        score += (rsi - 50.0) / 50.0 * 0.3  # 30% RSI
        score += (roc / 10.0) * 0.3  # 30% ROC

        if trend == "bullish":
            score *= 1.1
        elif trend == "bearish":
            score *= -1.1

        score = max(-1.0, min(1.0, score))

        components = {
            "return": return_pct,
            "rsi": rsi,
            "roc": roc,
            "ema_trend": 1.0 if trend == "bullish" else -1.0 if trend == "bearish" else 0.0,
        }

        score_obj = MomentumScore(
            symbol=symbol,
            score=score,
            trend=trend,
            components=components,
        )

        self._momentum_scores[symbol] = score_obj
        return score_obj

    def rank_momentum(
        self,
        price_data: dict[str, list[float]],
        lookback: int = 20,
    ) -> list[MomentumScore]:
        """Rank symbols by momentum."""
        scores = []
        for symbol, prices in price_data.items():
            score = self.calculate_momentum_score(symbol, prices, lookback)
            scores.append(score)

        scores.sort(key=lambda s: -s.score)
        return scores

    def _calculate_rsi(self, prices: list[float], period: int = 14) -> float:
        """Calculate RSI."""
        if len(prices) < period + 1:
            return 50.0

        gains = []
        losses = []

        for i in range(1, len(prices)):
            change = prices[i] - prices[i-1]
            gains.append(max(0, change))
            losses.append(max(0, -change))

        recent_gains = gains[-period:]
        recent_losses = losses[-period:]

        avg_gain = sum(recent_gains) / period
        avg_loss = sum(recent_losses) / period

        if avg_loss == 0:
            return 100.0

        rs = avg_gain / avg_loss
        rsi = 100.0 - (100.0 / (1.0 + rs))
        return rsi

    def _calculate_ema(self, prices: list[float], period: int) -> float:
        """Calculate Exponential Moving Average."""
        if len(prices) < period:
            return sum(prices) / len(prices) if prices else 0.0

        multiplier = 2.0 / (period + 1.0)
        ema = sum(prices[:period]) / period

        for price in prices[period:]:
            ema = (price - ema) * multiplier + ema

        return ema

    # ── Momentum Reversal ───────────────────────────────────────────────────

    def detect_reversal(
        self,
        symbol: str,
        prices: list[float],
        volumes: list[float] | None = None,
    ) -> ReversalSignal | None:
        """Detect momentum reversal."""
        if len(prices) < 30:
            return None

        score = self.calculate_momentum_score(symbol, prices)
        current_trend = score.trend

        # Check for exhaustion
        exhaustion_indicators = []
        rsi = self._calculate_rsi(prices, 14)

        if current_trend == "bullish":
            # Look for overbought + bearish divergence
            if rsi > 70:
                exhaustion_indicators.append("RSI overbought")

            # Check for lower highs
            recent = prices[-20:]
            if len(recent) >= 5:
                highs = [max(recent[i:i+5]) for i in range(len(recent)-5)]
                if len(highs) >= 3 and highs[-1] < highs[-3]:
                    exhaustion_indicators.append("Lower highs pattern")

            if exhaustion_indicators:
                return ReversalSignal(
                    symbol=symbol,
                    direction="bearish_reversal",
                    current_trend="bullish",
                    reversal_strength=len(exhaustion_indicators) / 3.0,
                    exhaustion_indicators=exhaustion_indicators,
                    confidence=min(0.8, 0.3 + len(exhaustion_indicators) * 0.2),
                )

        elif current_trend == "bearish":
            if rsi < 30:
                exhaustion_indicators.append("RSI oversold")

            recent = prices[-20:]
            if len(recent) >= 5:
                lows = [min(recent[i:i+5]) for i in range(len(recent)-5)]
                if len(lows) >= 3 and lows[-1] > lows[-3]:
                    exhaustion_indicators.append("Higher lows pattern")

            if exhaustion_indicators:
                return ReversalSignal(
                    symbol=symbol,
                    direction="bullish_reversal",
                    current_trend="bearish",
                    reversal_strength=len(exhaustion_indicators) / 3.0,
                    exhaustion_indicators=exhaustion_indicators,
                    confidence=min(0.8, 0.3 + len(exhaustion_indicators) * 0.2),
                )

        return None

    # ── Multi-Timeframe Confirmation ────────────────────────────────────────

    def confirm_multi_timeframe(
        self,
        symbol: str,
        signals_by_tf: dict[str, BreakoutSignal],
    ) -> dict[str, Any]:
        """Confirm signals across multiple timeframes."""
        aligned = {"long": 0, "short": 0, "none": 0}

        for tf, signal in signals_by_tf.items():
            if signal:
                aligned[signal.direction] = aligned.get(signal.direction, 0) + 1

        # Determine overall direction
        if aligned["long"] >= 2 and aligned["long"] > aligned["short"]:
            overall = "long"
        elif aligned["short"] >= 2 and aligned["short"] > aligned["long"]:
            overall = "short"
        else:
            overall = "none"

        # Calculate confidence
        tf_count = len(signals_by_tf)
        aligned_count = aligned.get(overall, 0) if overall != "none" else 0
        confidence = aligned_count / tf_count if tf_count > 0 else 0.0

        return {
            "symbol": symbol,
            "overall_direction": overall,
            "alignment_score": aligned_count / max(1, tf_count),
            "confidence": confidence,
            "by_timeframe": {
                tf: signal.direction if signal else "none"
                for tf, signal in signals_by_tf.items()
            },
        }
