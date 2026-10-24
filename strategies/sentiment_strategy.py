"""
Stratex/strategies/sentiment_strategy.py — Market Sentiment Analysis Strategy.

Implements:
1. News Sentiment: analyze news articles for market-moving sentiment
2. Social Sentiment: analyze social media sentiment
3. Fear & Greed Index: composite market sentiment indicator
4. Sentiment Divergence: detect divergence between price and sentiment
5. Sentiment Momentum: track how sentiment changes over time
6. Contrarian Signals: detect when sentiment is extreme (contrarian opportunities)
"""
from __future__ import annotations

import math
import time
from dataclasses import dataclass, field
from typing import Any


@dataclass
class SentimentScore:
    """A sentiment score."""
    source: str  # news, social, forum
    score: float  # -1 to 1
    confidence: float
    articles_analyzed: int
    timestamp: float
    dominant_topics: list[str] = field(default_factory=list)


@dataclass
class FearGreedIndex:
    """Fear & Greed Index."""
    value: int  # 0-100
    classification: str  # Extreme Fear, Fear, Neutral, Greed, Extreme Greed
    components: dict[str, float]
    timestamp: float
    recommendation: str


@dataclass
class SentimentSignal:
    """A trading signal based on sentiment."""
    symbol: str
    direction: str  # long, short, none
    sentiment_score: float
    fear_greed: int
    confidence: float
    reason: str
    contrarian: bool


@dataclass
class SentimentHistory:
    """Historical sentiment tracking."""
    symbol: str
    history: list[tuple[float, float]]  # (timestamp, score)
    trend: str  # rising, falling, stable
    volatility: float


class SentimentStrategy:
    """Market sentiment analysis strategy."""

    POSITIVE_WORDS = {
        "beat", "bounce", "rally", "surge", "gain", "rise", "up", "positive",
        "growth", "profit", "win", "success", "record", "strong", "improve",
        "buy", "bullish", "optimistic", "boost", "climb", "jump", "soar",
    }

    NEGATIVE_WORDS = {
        "drop", "fall", "crash", "plunge", "loss", "decline", "down", "negative",
        "layoff", "bankruptcy", "miss", "weak", "worse", "sell", "bearish",
        "pessimistic", "slump", "tumble", "collapse", "fear", "panic",
        "downgrade", "cut", "reduce",
    }

    def __init__(self):
        self._sentiment_scores: dict[str, SentimentScore] = {}
        self._fear_greed: dict[str, FearGreedIndex] = {}
        self._history: dict[str, SentimentHistory] = {}

    # ── News Sentiment ──────────────────────────────────────────────────────

    def analyze_news(
        self,
        symbol: str,
        articles: list[dict],
    ) -> SentimentScore:
        """Analyze news articles for sentiment."""
        total_score = 0.0
        total_confidence = 0.0
        topics: set[str] = set()

        for article in articles:
            text = article.get("text", "").lower()
            words = set(text.split())

            pos = len(words & self.POSITIVE_WORDS)
            neg = len(words & self.NEGATIVE_WORDS)

            score = (pos - neg) / max(1, pos + neg)
            confidence = min(1.0, (pos + neg) / 20.0)

            total_score += score * confidence
            total_confidence += confidence

            # Extract topics
            if article.get("topic"):
                topics.add(article["topic"])

        avg_score = total_score / max(1, total_confidence)
        avg_score = max(-1.0, min(1.0, avg_score))

        sentiment = SentimentScore(
            source=f"news_{symbol}",
            score=avg_score,
            confidence=total_confidence / max(1, len(articles)),
            articles_analyzed=len(articles),
            timestamp=time.time(),
            dominant_topics=list(topics)[:5],
        )

        self._sentiment_scores[sentiment.source] = sentiment

        # Update history
        self._update_history(symbol, avg_score)

        return sentiment

    # ── Social Sentiment ────────────────────────────────────────────────────

    def analyze_social(
        self,
        symbol: str,
        posts: list[dict],
    ) -> SentimentScore:
        """Analyze social media sentiment."""
        total_score = 0.0
        total_confidence = 0.0

        for post in posts:
            text = post.get("text", "").lower()
            words = set(text.split())

            pos = len(words & self.POSITIVE_WORDS)
            neg = len(words & self.NEGATIVE_WORDS)

            score = (pos - neg) / max(1, pos + neg)
            confidence = min(1.0, (pos + neg) / 10.0)

            # Weight by engagement
            engagement = post.get("likes", 0) + post.get("shares", 0) * 2
            weight = min(2.0, 1.0 + engagement / 1000.0)

            total_score += score * confidence * weight
            total_confidence += confidence * weight

        avg_score = total_score / max(1, total_confidence)
        avg_score = max(-1.0, min(1.0, avg_score))

        sentiment = SentimentScore(
            source=f"social_{symbol}",
            score=avg_score,
            confidence=total_confidence / max(1, len(posts)),
            articles_analyzed=len(posts),
            timestamp=time.time(),
        )

        self._sentiment_scores[sentiment.source] = sentiment
        self._update_history(symbol, avg_score)

        return sentiment

    # ── Fear & Greed Index ───────────────────────────────────────────────────

    def calculate_fear_greed(
        self,
        market_data: dict[str, Any],
    ) -> FearGreedIndex:
        """Calculate Fear & Greed Index."""
        # Components
        # 1. Market momentum (25%)
        momentum = market_data.get("momentum", 0.0)
        # 2. Volatility (20%)
        volatility = market_data.get("volatility", 0.0)
        fear_vol = max(0.0, 1.0 - volatility / 0.05)  # Higher vol = more fear
        # 3. Sentiment (25%)
        sentiment = market_data.get("sentiment", 0.0)
        # 4. Market breadth (15%)
        breadth = market_data.get("breadth", 0.5)
        # 5. Safe haven demand (15%)
        safe_haven = market_data.get("safe_haven_demand", 0.0)

        # Combine into index (0-100)
        index = (
            momentum * 25 +
            (1.0 - fear_vol) * 20 +
            sentiment * 50 +
            breadth * 15 +
            safe_haven * 15
        )

        index = max(0, min(100, index))

        if index < 25:
            classification = "Extreme Fear"
            recommendation = "Contrarian buy signal - market is overly fearful"
        elif index < 45:
            classification = "Fear"
            recommendation = "Selective buying - sentiment is negative"
        elif index < 55:
            classification = "Neutral"
            recommendation = "Hold positions - market is balanced"
        elif index < 75:
            classification = "Greed"
            recommendation = "Consider trimming - sentiment is overly optimistic"
        else:
            classification = "Extreme Greed"
            recommendation = "Contrarian sell signal - market is overly greedy"

        fg = FearGreedIndex(
            value=int(index),
            classification=classification,
            components={
                "momentum": momentum,
                "volatility": fear_vol,
                "sentiment": sentiment,
                "breadth": breadth,
                "safe_haven": safe_haven,
            },
            timestamp=time.time(),
            recommendation=recommendation,
        )

        self._fear_greed["composite"] = fg
        return fg

    # ── Sentiment Divergence ────────────────────────────────────────────────

    def detect_divergence(
        self,
        symbol: str,
        price_change: float,
        sentiment_score: float,
    ) -> dict[str, Any]:
        """Detect divergence between price and sentiment."""
        # Normalize sentiment to comparable range
        sentiment_pct = sentiment_score * 100

        # Calculate divergence
        divergence = sentiment_pct - price_change * 100

        # Interpret
        if divergence > 20:
            divergence_type = "bullish_divergence"
            signal = "Price down but sentiment positive - potential reversal"
        elif divergence < -20:
            divergence_type = "bearish_divergence"
            signal = "Price up but sentiment negative - potential reversal"
        else:
            divergence_type = "aligned"
            signal = "Price and sentiment aligned"

        return {
            "symbol": symbol,
            "price_change": price_change,
            "sentiment_score": sentiment_score,
            "divergence": divergence,
            "divergence_type": divergence_type,
            "signal": signal,
        }

    # ── Sentiment Momentum ──────────────────────────────────────────────────

    def calculate_sentiment_momentum(
        self,
        symbol: str,
        current_score: float,
        prev_score: float,
    ) -> str:
        """Calculate sentiment momentum."""
        change = current_score - prev_score

        if change > 0.2:
            return "strongly_rising"
        elif change > 0.05:
            return "rising"
        elif change > -0.05:
            return "stable"
        elif change > -0.2:
            return "falling"
        else:
            return "strongly_falling"

    def _update_history(self, symbol: str, score: float):
        """Update sentiment history."""
        if symbol not in self._history:
            self._history[symbol] = SentimentHistory(
                symbol=symbol,
                history=[],
                trend="stable",
                volatility=0.0,
            )

        hist = self._history[symbol]
        hist.history.append((time.time(), score))

        # Keep last 100 entries
        if len(hist.history) > 100:
            hist.history = hist.history[-100:]

        # Calculate trend
        if len(hist.history) >= 2:
            prev = hist.history[-2][1]
            change = score - prev
            hist.trend = self.calculate_sentiment_momentum(symbol, score, prev)

        # Calculate volatility
        if len(hist.history) >= 2:
            scores = [s for _, s in hist.history]
            mean = sum(scores) / len(scores)
            hist.volatility = math.sqrt(
                sum((s - mean) ** 2 for s in scores) / len(scores)
            )

    # ── Contrarian Signals ───────────────────────────────────────────────────

    def generate_contrarian_signal(
        self,
        symbol: str,
        price_change: float,
        sentiment_score: float,
        fear_greed: int,
    ) -> SentimentSignal:
        """Generate contrarian trading signal."""
        contrarian = False
        direction = "none"
        confidence = 0.0
        reason = ""

        # Extreme fear = buy opportunity (contrarian)
        if fear_greed < 25 and sentiment_score < -0.5:
            direction = "long"
            contrarian = True
            confidence = min(0.8, (25 - fear_greed) / 25.0 + abs(sentiment_score) * 0.2)
            reason = f"Extreme fear ({fear_greed}) with negative sentiment - contrarian buy"

        # Extreme greed = sell opportunity (contrarian)
        elif fear_greed > 75 and sentiment_score > 0.5:
            direction = "short"
            contrarian = True
            confidence = min(0.8, (fear_greed - 75) / 25.0 + abs(sentiment_score) * 0.2)
            reason = f"Extreme greed ({fear_greed}) with positive sentiment - contrarian sell"

        # Sentiment divergence
        divergence = self.detect_divergence(symbol, price_change, sentiment_score)
        if divergence["divergence_type"] == "bullish_divergence":
            direction = "long"
            contrarian = True
            confidence = max(confidence, min(0.7, divergence["divergence"] / 50.0))
            reason = divergence["signal"]
        elif divergence["divergence_type"] == "bearish_divergence":
            direction = "short"
            contrarian = True
            confidence = max(confidence, min(0.7, abs(divergence["divergence"]) / 50.0))
            reason = divergence["signal"]

        return SentimentSignal(
            symbol=symbol,
            direction=direction,
            sentiment_score=sentiment_score,
            fear_greed=fear_greed,
            confidence=confidence,
            reason=reason,
            contrarian=contrarian,
        )

    # ── Composite Sentiment ─────────────────────────────────────────────────

    def get_composite_sentiment(self, symbol: str) -> dict[str, Any]:
        """Get composite sentiment across all sources."""
        scores = []
        for source, score in self._sentiment_scores.items():
            if symbol in source:
                scores.append(score)

        if not scores:
            return {"symbol": symbol, "composite": 0.0, "sources": 0}

        composite = sum(s.score * s.confidence for s in scores) / sum(
            s.confidence for s in scores
        )

        return {
            "symbol": symbol,
            "composite": composite,
            "sources": len(scores),
            "news": next((s.score for s in scores if "news" in s.source), 0.0),
            "social": next((s.score for s in scores if "social" in s.source), 0.0),
        }

    # ── Reporting ──────────────────────────────────────────────────────────

    def get_sentiment_report(self) -> dict[str, Any]:
        """Generate a sentiment report."""
        return {
            "total_sources": len(self._sentiment_scores),
            "symbols_tracked": len(self._history),
            "fear_greed": self._fear_greed.get("composite", None).__dict__ if self._fear_greed else None,
        }
