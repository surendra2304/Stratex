"""
Stratex/api/sentiment_routes.py — Stratex Sentiment Analysis (Flask Blueprint).
"""
from flask import Blueprint, jsonify, request

from execution.sentiment_analysis_engine import SentimentAnalysisEngine

sentiment_bp = Blueprint("sentiment", __name__, url_prefix="/api/v1/trading/sentiment")

_sentiment_engine = SentimentAnalysisEngine()


@sentiment_bp.route("/data", methods=["POST"])
def add_sentiment_data():
    """Add sentiment data."""
    data = request.get_json() or {}
    result = _sentiment_engine.add_sentiment_data(
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
    news = _sentiment_engine.analyze_news_impact(
        symbol=data["symbol"],
        title=data["title"],
        impact_score=data.get("impact_score", 0.5),
    )
    return jsonify({"news_id": news.news_id, "direction": news.impact_direction, "estimated_move_pct": news.estimated_price_move_pct})


@sentiment_bp.route("/signals/{symbol}", methods=["GET"])
def generate_signal(symbol):
    """Generate a sentiment signal."""
    signal = _sentiment_engine.generate_signal(symbol)
    return jsonify({"symbol": signal.symbol, "type": signal.signal_type, "confidence": signal.confidence, "action": signal.suggested_action})


@sentiment_bp.route("/contrarian/{symbol}", methods=["GET"])
def generate_contrarian(symbol):
    """Generate a contrarian indicator."""
    result = _sentiment_engine.generate_contrarian(symbol)
    return jsonify({"symbol": result.symbol, "consensus": result.consensus_sentiment, "extreme": result.extreme_level})


@sentiment_bp.route("/report", methods=["GET"])
def get_report():
    """Get sentiment analysis report."""
    return jsonify(_sentiment_engine.get_report())
