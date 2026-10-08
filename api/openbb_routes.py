"""
api/openbb_routes.py — REST API Blueprint exposing OpenBB Intelligence Endpoints.
Endpoints:
- GET /api/v1/openbb/status              : Provider health & cache readiness
- GET /api/v1/openbb/crypto/global       : Global market cap, BTC dominance, volume
- GET /api/v1/openbb/crypto/sentiment    : Crypto Fear & Greed index
- GET /api/v1/openbb/crypto/trending     : Top trending cryptocurrencies
- GET /api/v1/openbb/economy/macro       : Cross-asset macroeconomic regime
- GET /api/v1/openbb/economy/indicators  : All macro indicators (DXY, 10Y Yield, VIX, Gold)
- GET /api/v1/openbb/quantitative/metrics: Sharpe, Sortino, Calmar, VaR (95%/99%), CVaR
"""

from dataclasses import asdict
from flask import Blueprint, jsonify, request

from api.data_shapes import format_api_response
from stratex_openbb import obb
from stratex_openbb.client import OFFLINE_FALLBACK_SOURCE

openbb_bp = Blueprint("openbb_api", __name__, url_prefix="/api/v1/openbb")


@openbb_bp.route("/status", methods=["GET"])
def get_openbb_status():
    """Health check for OpenBB free providers."""
    health = obb.health_check()
    return jsonify(format_api_response(health))


@openbb_bp.route("/crypto/global", methods=["GET"])
def get_crypto_global():
    """Global crypto market aggregates."""
    refresh = request.args.get("refresh", "false").lower() == "true"
    overview = obb.crypto.overview(force_refresh=refresh)
    return jsonify(format_api_response(asdict(overview)))


@openbb_bp.route("/crypto/sentiment", methods=["GET"])
def get_crypto_sentiment():
    """Crypto Fear & Greed index reading."""
    refresh = request.args.get("refresh", "false").lower() == "true"
    reading = obb.crypto.sentiment(force_refresh=refresh)
    return jsonify(format_api_response(asdict(reading)))


@openbb_bp.route("/crypto/trending", methods=["GET"])
def get_crypto_trending():
    """Top trending cryptocurrencies."""
    refresh = request.args.get("refresh", "false").lower() == "true"
    trending = obb.crypto.trending(force_refresh=refresh)
    return jsonify(format_api_response(trending))


@openbb_bp.route("/economy/macro", methods=["GET"])
def get_macro_regime():
    """Cross-asset macroeconomic regime determination.

    When its inputs are offline placeholders the regime is labeled as such
    (``data_quality``/``fallback_inputs``) and its confidence is reported as 0.
    """
    refresh = request.args.get("refresh", "false").lower() == "true"
    regime = obb.economy.regime(force_refresh=refresh)
    data = asdict(regime)
    fallback_inputs = []
    try:
        for key, snap in obb.economy.indicators().items():
            if getattr(snap, "source", None) == OFFLINE_FALLBACK_SOURCE:
                fallback_inputs.append(key)
        if getattr(obb.crypto.sentiment(), "source", None) == OFFLINE_FALLBACK_SOURCE:
            fallback_inputs.append("fear_greed")
    except Exception:
        fallback_inputs.append("UNKNOWN")
    data["data_quality"] = "FALLBACK_PLACEHOLDER" if fallback_inputs else "LIVE"
    data["fallback_inputs"] = fallback_inputs
    if fallback_inputs:
        data["confidence"] = 0.0
    return jsonify(format_api_response(data))


@openbb_bp.route("/economy/indicators", methods=["GET"])
def get_macro_indicators():
    """All macro indicator snapshots."""
    refresh = request.args.get("refresh", "false").lower() == "true"
    indicators = obb.economy.indicators(force_refresh=refresh)
    data = {k: asdict(v) for k, v in indicators.items()}
    return jsonify(format_api_response(data))


@openbb_bp.route("/quantitative/metrics", methods=["GET"])
def get_quantitative_metrics():
    """Computes quantitative risk and volatility metrics for a given symbol."""
    symbol = request.args.get("symbol", "BTCUSDT").upper().strip()
    timeframe = request.args.get("timeframe", "1h").strip()
    if not symbol.isalnum() or len(symbol) > 20:
        return jsonify({"status": "ERROR", "error": "INVALID_SYMBOL", "message": "symbol must be alphanumeric, e.g. BTCUSDT"}), 400
    try:
        limit = int(request.args.get("limit", 100))
    except (TypeError, ValueError):
        return jsonify({"status": "ERROR", "error": "INVALID_LIMIT", "message": "'limit' must be an integer"}), 400
    limit = min(max(limit, 5), 500)

    # Fetch public klines
    df = obb.crypto.price.historical(symbol=symbol, timeframe=timeframe, limit=limit)
    if df is None or df.empty or len(df) < 5:
        # Previously a hardcoded "baseline" price series and constant volatility
        # numbers were returned here with HTTP 200. No live klines -> no metrics.
        return jsonify({
            "status": "ERROR",
            "error": "DATA_UNAVAILABLE",
            "message": f"Fewer than 5 live klines available for {symbol} ({timeframe}); metrics not computed.",
        }), 503
    risk = obb.quantitative.risk_metrics(df["close"].to_numpy(), symbol=symbol)
    vols = obb.quantitative.volatility_estimators(df, symbol=symbol, timeframe=timeframe)

    return jsonify(format_api_response({
        "risk_metrics": asdict(risk),
        "volatility": asdict(vols)
    }))
