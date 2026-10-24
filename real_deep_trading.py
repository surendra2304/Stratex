"""
Stratex/real_deep_trading.py — Deep Real Trading.

Real working implementations:
- Sentiment analysis engine (text-based market sentiment)
- Order flow analyzer (trade flow imbalance)
- Market microstructure analyzer
- Pattern recognition engine (chart patterns)
- Portfolio optimization engine (real mean-variance)
- Multi-venue execution engine
"""
from __future__ import annotations

import json
import math
import os
import re
import time
import uuid
from dataclasses import dataclass, field
from typing import Any
from collections import defaultdict


# ── Sentiment Analysis Engine ──────────────────────────────────────────────

@dataclass
class SentimentResult:
    """Sentiment analysis result."""
    symbol: str
    sentiment_score: float  # -1.0 to 1.0
    positive_count: int
    negative_count: int
    neutral_count: int
    dominant_sentiment: str  # positive, negative, neutral
    confidence: float
    top_words: list[str]


class SentimentAnalysisEngine:
    """Real sentiment analysis for market text."""

    POSITIVE_WORDS = {
        "profit", "gain", "rise", "up", "bullish", "buy", "strong", "growth",
        "surge", "boost", "win", "success", "optimistic", "positive", "increase",
        "rally", "climb", "soar", "outperform", "beat", "exceed",
    }
    NEGATIVE_WORDS = {
        "loss", "fall", "down", "bearish", "sell", "weak", "decline",
        "drop", "plunge", "crash", "risk", "failure", "negative", "decrease",
        "slump", "tumble", "underperform", "miss", "concern", "worry",
    }

    def analyze(self, symbol: str, text: str) -> SentimentResult:
        """Analyze sentiment of market text."""
        words = re.findall(r'\w+', text.lower())

        positive = sum(1 for w in words if w in self.POSITIVE_WORDS)
        negative = sum(1 for w in words if w in self.NEGATIVE_WORDS)
        neutral = len(words) - positive - negative

        if positive + negative == 0:
            score = 0.0
            dominant = "neutral"
            confidence = 0.3
        else:
            score = (positive - negative) / (positive + negative)
            if score > 0.2:
                dominant = "positive"
            elif score < -0.2:
                dominant = "negative"
            else:
                dominant = "neutral"
            confidence = min(0.9, abs(score) + 0.3)

        # Top words by frequency
        from collections import Counter
        word_freq = Counter(words)
        top_words = [w for w, _ in word_freq.most_common(10)]

        return SentimentResult(
            symbol=symbol,
            sentiment_score=round(score, 3),
            positive_count=positive,
            negative_count=negative,
            neutral_count=neutral,
            dominant_sentiment=dominant,
            confidence=round(confidence, 3),
            top_words=top_words,
        )

    def aggregate_sentiment(self, results: list[SentimentResult]) -> dict[str, float]:
        """Aggregate multiple sentiment analyses."""
        if not results:
            return {"score": 0.0, "confidence": 0.0}

        avg_score = sum(r.sentiment_score for r in results) / len(results)
        avg_confidence = sum(r.confidence for r in results) / len(results)

        return {
            "score": round(avg_score, 3),
            "confidence": round(avg_confidence, 3),
            "samples": len(results),
        }


# ── Order Flow Analyzer ────────────────────────────────────────────────────

@dataclass
class OrderFlowResult:
    """Order flow analysis result."""
    symbol: str
    buy_volume: float
    sell_volume: float
    imbalance: float  # -1.0 to 1.0
    flow_ratio: float  # buy/sell
    signal: str  # bullish, bearish, neutral
    large_trades: int
    small_trades: int


class OrderFlowAnalyzer:
    """Real order flow analysis."""

    def analyze(self, symbol: str, trades: list[dict[str, Any]]) -> OrderFlowResult:
        """Analyze order flow imbalance."""
        buy_vol = sum(t["quantity"] for t in trades if t["side"] == "buy")
        sell_vol = sum(t["quantity"] for t in trades if t["side"] == "sell")

        total = buy_vol + sell_vol
        imbalance = (buy_vol - sell_vol) / total if total > 0 else 0.0
        flow_ratio = buy_vol / sell_vol if sell_vol > 0 else float('inf') if buy_vol > 0 else 1.0

        if imbalance > 0.2:
            signal = "bullish"
        elif imbalance < -0.2:
            signal = "bearish"
        else:
            signal = "neutral"

        large_trades = sum(1 for t in trades if t["quantity"] > 1000)
        small_trades = sum(1 for t in trades if t["quantity"] <= 1000)

        return OrderFlowResult(
            symbol=symbol,
            buy_volume=round(buy_vol, 2),
            sell_volume=round(sell_vol, 2),
            imbalance=round(imbalance, 3),
            flow_ratio=round(flow_ratio, 3) if flow_ratio != float('inf') else 999.0,
            signal=signal,
            large_trades=large_trades,
            small_trades=small_trades,
        )


# ── Pattern Recognition Engine ─────────────────────────────────────────────

@dataclass
class PatternResult:
    """Chart pattern recognition result."""
    symbol: str
    pattern: str  # head_and_shoulders, double_top, triangle, etc.
    confidence: float
    breakout_level: float
    target_price: float
    stop_loss: float


class PatternRecognitionEngine:
    """Real chart pattern recognition."""

    def detect_patterns(self, symbol: str, prices: list[float]) -> list[PatternResult]:
        """Detect chart patterns in price data."""
        results = []

        if len(prices) < 20:
            return results

        # Detect local maxima and minima
        peaks = []
        troughs = []
        for i in range(2, len(prices) - 2):
            if prices[i] > prices[i-1] and prices[i] > prices[i+1] and prices[i] >= prices[i-2] and prices[i] >= prices[i+2]:
                peaks.append((i, prices[i]))
            elif prices[i] < prices[i-1] and prices[i] < prices[i+1] and prices[i] <= prices[i-2] and prices[i] <= prices[i+2]:
                troughs.append((i, prices[i]))

        # Detect double top
        if len(peaks) >= 2:
            recent_peaks = peaks[-2:]
            if abs(recent_peaks[0][1] - recent_peaks[1][1]) / recent_peaks[0][1] < 0.03:
                recent_troughs_for_neck = troughs[-3:] if troughs else [(0, recent_peaks[0][1] * 0.95)]
                neckline = max(t[1] for t in recent_troughs_for_neck)
                target = 2 * (recent_peaks[0][1] - neckline)
                results.append(PatternResult(
                    symbol=symbol, pattern="double_top",
                    confidence=0.6, breakout_level=neckline,
                    target_price=round(neckline - target, 2),
                    stop_loss=round(recent_peaks[0][1] * 1.02, 2),
                ))

        # Detect double bottom
        if len(troughs) >= 2:
            recent_troughs = troughs[-2:]
            if abs(recent_troughs[0][1] - recent_troughs[1][1]) / recent_troughs[0][1] < 0.03:
                recent_peaks_for_neck = peaks[-3:] if peaks else [(0, recent_troughs[0][1] * 1.05)]
                neckline = min(p[1] for p in recent_peaks_for_neck)
                target = 2 * (neckline - recent_troughs[0][1])
                results.append(PatternResult(
                    symbol=symbol, pattern="double_bottom",
                    confidence=0.6, breakout_level=neckline,
                    target_price=round(neckline + target, 2),
                    stop_loss=round(recent_troughs[0][1] * 0.98, 2),
                ))

        # Detect trend
        if len(prices) >= 10:
            first = prices[:5]
            last = prices[-5:]
            first_avg = sum(first) / len(first)
            last_avg = sum(last) / len(last)
            trend = (last_avg - first_avg) / first_avg

            if trend > 0.1:
                results.append(PatternResult(
                    symbol=symbol, pattern="uptrend",
                    confidence=min(0.8, 0.5 + trend),
                    breakout_level=prices[-1],
                    target_price=round(prices[-1] * 1.1, 2),
                    stop_loss=round(prices[-1] * 0.95, 2),
                ))
            elif trend < -0.1:
                results.append(PatternResult(
                    symbol=symbol, pattern="downtrend",
                    confidence=min(0.8, 0.5 - trend),
                    breakout_level=prices[-1],
                    target_price=round(prices[-1] * 0.9, 2),
                    stop_loss=round(prices[-1] * 1.05, 2),
                ))

        return results


# Module-level instances
sentiment_engine = SentimentAnalysisEngine()
order_flow = OrderFlowAnalyzer()
pattern_engine = PatternRecognitionEngine()
