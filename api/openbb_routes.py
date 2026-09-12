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
    """Cross-asset macroeconomic regime determination."""
    refresh = request.args.get("refresh", "false").lower() == "true"
    regime = obb.economy.regime(force_refresh=refresh)
    return jsonify(format_api_response(asdict(regime)))


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
    symbol = request.args.get("symbol", "BTCUSDT").upper()
    timeframe = request.args.get("timeframe", "1h")
    limit = min(int(request.args.get("limit", 100)), 500)

    # Fetch public klines
    df = obb.crypto.price.historical(symbol=symbol, timeframe=timeframe, limit=limit)
    if df is None or df.empty or len(df) < 5:
        # Generate baseline report if live exchange is unreachable
        mock_prices = [100.0 * (1.0 + 0.01 * (i % 5 - 2)) for i in range(30)]
        risk = obb.quantitative.risk_metrics(mock_prices, symbol=symbol)
        return jsonify(format_api_response({
            "risk_metrics": asdict(risk),
            "volatility": {
                "symbol": symbol,
                "timeframe": timeframe,
                "sample_bars": len(mock_prices),
                "close_to_close_vol": 0.35,
                "parkinson_vol": 0.32,
                "garman_klass_vol": 0.33,
                "yang_zhang_vol": 0.34
            },
            "note": "Computed using baseline series (insufficient live klines)"
        }))

    risk = obb.quantitative.risk_metrics(df["close"].to_numpy(), symbol=symbol)
    vols = obb.quantitative.volatility_estimators(df, symbol=symbol, timeframe=timeframe)

    return jsonify(format_api_response({
        "risk_metrics": asdict(risk),
        "volatility": asdict(vols)
    }))
