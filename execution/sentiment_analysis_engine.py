"""
Stratex/execution/sentiment_analysis_engine.py — Stratex Sentiment Analysis Engine.

Analyzes market sentiment from news, social media, and order flow data.
Provides sentiment scoring, trend detection, contrarian signals, and
sentiment-based trading recommendations.
"""
from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass, field
from typing import Any

from flask import Blueprint, jsonify, request

sentiment_bp = Blueprint("sentiment", __name__, url_prefix="/api/v1/trading/sentiment")


@dataclass
class SentimentData:
    """Sentiment data from a source."""
    data_id: str
    symbol: str
    source: str = ""  # news, social, order_flow, analyst
    sentiment_score: float = 0.0  # -1.0 to 1.0
    confidence: float = 0.0
    volume: int = 0
    content: str = ""
    published_at: float = 0.0


@dataclass
class SentimentAggregate:
    """Aggregated sentiment for a symbol."""
    symbol: str
    overall_score: float = 0.0
    bullish_count: int = 0
    bearish_count: int = 0
    neutral_count: int = 0
    avg_confidence: float = 0.0
    total_volume: int = 0
    trend: str = "stable"  # bullish, bearish, stable, volatile
    updated_at: float = 0.0


@dataclass
class SentimentSignal:
    """A trading signal based on sentiment."""
    signal_id: str
    symbol: str
    signal_type: str = ""  # bullish, bearish, contrarian, fade
    confidence: float = 0.0
    reason: str = ""
    expected_horizon: str = "short_term"  # short_term, medium_term, long_term
    suggested_action: str = "hold"  # buy, sell, hold, reduce, increase


@dataclass
class SentimentTrend:
    """Sentiment trend analysis."""
    symbol: str
    timeframe: str = "1d"
    trend_score: float = 0.0
    direction: str = "stable"  # bullish, bearish, stable
    momentum: float = 0.0
    volume_trend: str = "stable"
    pivot_detected: bool = False


@dataclass
class ContrarianIndicator:
    """Contrarian trading indicator."""
    indicator_id: str
    symbol: str
    consensus_sentiment: float = 0.0
    contrarian_signal: float = 0.0  # opposite of consensus
    extreme_level: str = "moderate"  # low, moderate, high, extreme
    recommendation: str = ""
    detected_at: float = 0.0


@dataclass
class NewsImpact:
    """News impact assessment."""
    news_id: str
    symbol: str
    title: str = ""
    impact_score: float = 0.0
    impact_direction: str = "neutral"  # positive, negative, neutral
    estimated_price_move_pct: float = 0.0
    relevance: float = 0.0
    published_at: float = 0.0


class SentimentAnalysisEngine:
    """Market sentiment analysis engine."""

    def __init__(self):
        self._data: list[SentimentData] = []
        self._aggregates: dict[str, SentimentAggregate] = {}
        self._signals: list[SentimentSignal] = []
        self._trends: dict[str, SentimentTrend] = {}
        self._contrarians: list[ContrarianIndicator] = []
        self._news: list[NewsImpact] = []

    def add_sentiment_data(
        self,
        symbol: str,
        source: str,
        sentiment_score: float,
        confidence: float = 0.7,
        volume: int = 100,
        content: str = "",
    ) -> SentimentData:
        """Add sentiment data."""
        data = SentimentData(
            data_id=hashlib.md5(
                f"sentiment:{symbol}:{source}:{content[:30]}:{time.time()}".encode()
            ).hexdigest()[:12],
            symbol=symbol,
            source=source,
            sentiment_score=sentiment_score,
            confidence=confidence,
            volume=volume,
            content=content,
            published_at=time.time(),
        )
        self._data.append(data)

        # Update aggregate
        agg = self._aggregates.get(symbol)
        if not agg:
            agg = SentimentAggregate(symbol=symbol)
            self._aggregates[symbol] = agg

        # Count bullish/bearish/neutral
        if sentiment_score > 0.2:
            agg.bullish_count += 1
        elif sentiment_score < -0.2:
            agg.bearish_count += 1
        else:
            agg.neutral_count += 1

        # Weighted average
        total_weight = agg.bullish_count + agg.bearish_count + agg.neutral_count
        agg.overall_score = (
            (agg.bullish_count - agg.bearish_count) / max(1, total_weight)
        )

        # Average confidence
        all_scores = [d.sentiment_score * d.confidence for d in self._data if d.symbol == symbol]
        all_confs = [d.confidence for d in self._data if d.symbol == symbol]
        agg.avg_confidence = sum(all_confs) / max(1, len(all_confs))

        # Total volume
        agg.total_volume = sum(d.volume for d in self._data if d.symbol == symbol)

        # Determine trend
        if agg.overall_score > 0.3:
            agg.trend = "bullish"
        elif agg.overall_score < -0.3:
            agg.trend = "bearish"
        else:
            agg.trend = "volatile" if abs(agg.overall_score) > 0.1 else "stable"

        agg.updated_at = time.time()

        return data

    def analyze_news_impact(
        self,
        symbol: str,
        title: str,
        impact_score: float = 0.5,
    ) -> NewsImpact:
        """Analyze news impact."""
        direction = "positive" if impact_score > 0.2 else "negative" if impact_score < -0.2 else "neutral"

        news = NewsImpact(
            news_id=hashlib.md5(
                f"news:{symbol}:{title[:30]}:{time.time()}".encode()
            ).hexdigest()[:12],
            symbol=symbol,
            title=title,
            impact_score=impact_score,
            impact_direction=direction,
            estimated_price_move_pct=impact_score * 2.0,
            relevance=0.7 + (ord(hashlib.md5(title.encode()).hexdigest()[:1]) % 25) / 100,
            published_at=time.time(),
        )
        self._news.append(news)

        # Add to sentiment data
        self.add_sentiment_data(
            symbol=symbol,
            source="news",
            sentiment_score=impact_score,
            confidence=news.relevance,
            content=title,
        )

        return news

    def generate_signal(self, symbol: str) -> SentimentSignal:
        """Generate a sentiment-based trading signal."""
        agg = self._aggregates.get(symbol)
        if not agg:
            raise Exception(f"No sentiment data for {symbol}")

        if agg.trend == "bullish":
            signal_type = "bullish"
            action = "buy"
            reason = "Strong bullish sentiment detected"
        elif agg.trend == "bearish":
            signal_type = "bearish"
            action = "sell"
            reason = "Strong bearish sentiment detected"
        else:
            signal_type = "contrarian"
            action = "hold"
            reason = "Mixed sentiment, consider contrarian approach"

        signal = SentimentSignal(
            signal_id=hashlib.md5(
                f"signal:{symbol}:{time.time()}".encode()
            ).hexdigest()[:12],
            symbol=symbol,
            signal_type=signal_type,
            confidence=agg.avg_confidence,
            reason=reason,
            expected_horizon="short_term",
            suggested_action=action,
        )

        self._signals.append(signal)
        return signal

    def generate_contrarian(self, symbol: str) -> ContrarianIndicator:
        """Generate a contrarian indicator."""
        agg = self._aggregates.get(symbol)
        if not agg:
            raise Exception(f"No sentiment data for {symbol}")

        consensus = agg.overall_score
        contrarian = -consensus

        if abs(consensus) > 0.7:
            extreme = "extreme"
            recommendation = "Strong contrarian signal: fade the consensus"
        elif abs(consensus) > 0.5:
            extreme = "high"
            recommendation = "Contrarian signal: consider opposite position"
        elif abs(consensus) > 0.3:
            extreme = "moderate"
            recommendation = "Moderate contrarian opportunity"
        else:
            extreme = "low"
            recommendation = "No strong contrarian signal"

        contrarian_ind = ContrarianIndicator(
            indicator_id=hashlib.md5(
                f"contrarian:{symbol}:{time.time()}".encode()
            ).hexdigest()[:12],
            symbol=symbol,
            consensus_sentiment=consensus,
            contrarian_signal=contrarian,
            extreme_level=extreme,
            recommendation=recommendation,
            detected_at=time.time(),
        )

        self._contrarians.append(contrarian_ind)
        return contrarian_ind

    def get_trend(self, symbol: str) -> SentimentTrend:
        """Get sentiment trend for a symbol."""
        agg = self._aggregates.get(symbol)
        if not agg:
            raise Exception(f"No sentiment data for {symbol}")

        recent = [d for d in self._data if d.symbol == symbol and d.published_at > time.time() - 86400]

        if len(recent) >= 2:
            # Compare recent sentiment to older
            recent_scores = [d.sentiment_score for d in recent]
            older_scores = [d.sentiment_score for d in self._data if d.symbol == symbol and d.published_at <= time.time() - 86400]

            recent_avg = sum(recent_scores) / len(recent_scores)
            older_avg = sum(older_scores) / max(1, len(older_scores))

            trend_score = recent_avg - older_avg
            direction = "bullish" if trend_score > 0.1 else "bearish" if trend_score < -0.1 else "stable"
            momentum = abs(trend_score) * 10
        else:
            trend_score = agg.overall_score
            direction = agg.trend
            momentum = abs(agg.overall_score) * 5

        trend = SentimentTrend(
            symbol=symbol,
            timeframe="1d",
            trend_score=round(trend_score, 4),
            direction=direction,
            momentum=round(momentum, 2),
            volume_trend="increasing" if agg.total_volume > 10000 else "stable",
            pivot_detected=abs(trend_score) > 0.2,
        )

        self._trends[symbol] = trend
        return trend

    def get_report(self) -> dict:
        """Get sentiment analysis report."""
        return {
            "total_data_points": len(self._data),
            "total_symbols": len(self._aggregates),
            "total_signals": len(self._signals),
            "total_contrarians": len(self._contrarians),
            "total_news": len(self._news),
            "bullish_symbols": sum(1 for a in self._aggregates.values() if a.trend == "bullish"),
            "bearish_symbols": sum(1 for a in self._aggregates.values() if a.trend == "bearish"),
            "neutral_symbols": sum(1 for a in self._aggregates.values() if a.trend == "stable"),
            "avg_sentiment": sum(a.overall_score for a in self._aggregates.values()) / max(1, len(self._aggregates)),
            "recent_signals": [
                {"symbol": s.symbol, "type": s.signal_type, "confidence": s.confidence, "action": s.suggested_action}
                for s in self._signals[-10:]
            ],
            "recent_contrarians": [
                {"symbol": c.symbol, "consensus": c.consensus_sentiment, "extreme": c.extreme_level}
                for c in self._contrarians[-5:]
            ],
            "recent_news": [
                {"symbol": n.symbol, "title": n.title[:50], "impact": n.impact_direction, "move_pct": n.estimated_price_move_pct}
                for n in self._news[-5:]
            ],
        }


sentiment_engine = SentimentAnalysisEngine()


@sentiment_bp.route("/data", methods=["POST"])
def add_sentiment_data():
    """Add sentiment data."""
    data = request.get_json() or {}
    result = sentiment_engine.add_sentiment_data(
        symbol=data["symbol"],
        source=data["source"],
        sentiment_score=data["sentiment_score"],
        confidence=data.get("confidence", 0.7),
        volume=data.get("volume", 100),
        content=data.get("content", ""),
    )
    return jsonify({"data_id": result.data_id, "symbol": result.symbol, "score": result.sentiment_score})


@sentiment_bp.route("/news", methods=["POST"])
def analyze_news_impact():
    """Analyze news impact."""
    data = request.get_json() or {}
    news = sentiment_engine.analyze_news_impact(
        symbol=data["symbol"],
        title=data["title"],
        impact_score=data.get("impact_score", 0.5),
    )
    return jsonify({"news_id": news.news_id, "direction": news.impact_direction, "estimated_move_pct": news.estimated_price_move_pct})


@sentiment_bp.route("/signals/{symbol}", methods=["GET"])
def generate_signal(symbol):
    """Generate a sentiment signal."""
    signal = sentiment_engine.generate_signal(symbol)
    return jsonify({"symbol": signal.symbol, "type": signal.signal_type, "confidence": signal.confidence, "action": signal.suggested_action})


@sentiment_bp.route("/contrarian/{symbol}", methods=["GET"])
def generate_contrarian(symbol):
    """Generate a contrarian indicator."""
    result = sentiment_engine.generate_contrarian(symbol)
    return jsonify({"symbol": result.symbol, "consensus": result.consensus_sentiment, "extreme": result.extreme_level, "recommendation": result.recommendation})


@sentiment_bp.route("/report", methods=["GET"])
def get_report():
    """Get sentiment analysis report."""
    return jsonify(sentiment_engine.get_report())
