"""api/ccxt_routes.py

REST API Blueprint exposing CCXT Multi-Exchange Endpoints:
- GET /api/v1/ccxt/status
- GET /api/v1/ccxt/exchanges
- GET /api/v1/ccxt/ticker
- GET /api/v1/ccxt/arbitrage
- GET /api/v1/ccxt/depth
- GET /api/v1/ccxt/funding
"""

from __future__ import annotations

from datetime import datetime, timezone
from dataclasses import asdict
from flask import Blueprint, jsonify, request

from stratex_ccxt_adapter import ccxt_hub

ccxt_bp = Blueprint("ccxt_bp", __name__, url_prefix="/api/v1/ccxt")


@ccxt_bp.route("/status", methods=["GET"])
def get_status():
    """Returns overall CCXTHub status and initialized adapters."""
    return jsonify({
        "status": "OK",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "data": ccxt_hub.get_status(),
    }), 200


@ccxt_bp.route("/exchanges", methods=["GET"])
def list_exchanges():
    """Lists supported free unauthenticated exchange connectors."""
    return jsonify({
        "status": "OK",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "data": ccxt_hub.list_exchanges(),
    }), 200


@ccxt_bp.route("/ticker", methods=["GET"])
def get_ticker():
    """Fetches normalized ticker for a symbol from a specified exchange."""
    symbol = request.args.get("symbol", "BTCUSDT")
    exchange = request.args.get("exchange", "binance").lower()
    try:
        adapter = ccxt_hub.get_exchange(exchange)
        ticker = adapter.fetch_ticker(symbol)
        return jsonify({
            "status": "OK",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "data": asdict(ticker),
        }), 200
    except Exception as e:
        return jsonify({
            "status": "ERROR",
            "message": str(e),
        }), 500


@ccxt_bp.route("/arbitrage", methods=["GET"])
def get_arbitrage():
    """Scans and compares prices across multiple exchanges to detect arbitrage spreads."""
    symbol = request.args.get("symbol", "BTCUSDT")
    opp = ccxt_hub.scan_arbitrage(symbol)
    return jsonify({
        "status": "OK",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "data": asdict(opp),
    }), 200


@ccxt_bp.route("/depth", methods=["GET"])
def get_depth():
    """Analyzes orderbook depth, liquidity, and volume-weighted micro-price."""
    symbol = request.args.get("symbol", "BTCUSDT")
    exchange = request.args.get("exchange", "binance").lower()
    levels = int(request.args.get("levels", 15))
    analysis = ccxt_hub.analyze_depth(symbol, exchange_id=exchange, depth_levels=levels)
    return jsonify({
        "status": "OK",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "data": asdict(analysis),
    }), 200


@ccxt_bp.route("/funding", methods=["GET"])
def get_funding():
    """Compares perpetual futures funding rates across exchanges."""
    symbol = request.args.get("symbol", "BTCUSDT")
    funding = ccxt_hub.compare_funding_rates(symbol)
    return jsonify({
        "status": "OK",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "data": asdict(funding),
    }), 200
