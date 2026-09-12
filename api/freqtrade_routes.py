"""api/freqtrade_routes.py

REST API Blueprint exposing Freqtrade Adapter Endpoints:
- GET /api/v1/freqtrade/status
- GET /api/v1/freqtrade/strategies
- GET /api/v1/freqtrade/pairlists/evaluate
- GET /api/v1/freqtrade/protections/status
- POST /api/v1/freqtrade/backtest
"""

from __future__ import annotations

from datetime import datetime, timezone
from flask import Blueprint, jsonify, request
import pandas as pd

from stratex_freqtrade_adapter import ft
from stratex_freqtrade_adapter.strategy.adapter import FreqtradeStrategyAdapter

freqtrade_bp = Blueprint("freqtrade_bp", __name__, url_prefix="/api/v1/freqtrade")


@freqtrade_bp.route("/status", methods=["GET"])
def get_status():
    """Returns the overall operational health of the Freqtrade integration."""
    status = ft.status()
    return jsonify({
        "status": "OK",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "data": status,
    }), 200


@freqtrade_bp.route("/strategies", methods=["GET"])
def get_strategies():
    """Returns the list of registered Freqtrade strategies and their parameters."""
    strategies = ft.strategies.list()
    return jsonify({
        "status": "OK",
        "count": len(strategies),
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "data": strategies,
    }), 200


@freqtrade_bp.route("/pairlists/evaluate", methods=["GET"])
def evaluate_pairlist():
    """Dynamically evaluates top trading pairs by 24h volume and price/spread filters."""
    limit = int(request.args.get("limit", 15))
    try:
        pairs = ft.pairlists.evaluate_volume(number_assets=limit)
        return jsonify({
            "status": "OK",
            "count": len(pairs),
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "source": "Binance Public 24h Tickers (Free / Unauthenticated)",
            "data": pairs,
        }), 200
    except Exception as e:
        return jsonify({
            "status": "ERROR",
            "message": str(e),
        }), 500


@freqtrade_bp.route("/protections/status", methods=["GET"])
def get_protections_status():
    """Returns the state of all pair locks, cooldowns, and drawdown protections."""
    status = ft.protections.get_status()
    return jsonify({
        "status": "OK",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "data": status,
    }), 200


@freqtrade_bp.route("/backtest", methods=["POST"])
def run_backtest():
    """Executes an on-demand backtest of a Freqtrade strategy on historical/synthetic data."""
    body = request.get_json(silent=True) or {}
    strat_name = body.get("strategy", "sample_strategy")
    symbol = body.get("symbol", "BTCUSDT")
    timeframe = body.get("timeframe", "5m")
    limit = int(body.get("candles", 200))

    strategy_inst = ft.strategies.create(strat_name)
    if not strategy_inst:
        return jsonify({
            "status": "ERROR",
            "message": f"Strategy '{strat_name}' not found in registry.",
        }), 404

    # Fetch data via free downloader
    df = ft.data.fetch_ohlcv(symbol=symbol, timeframe=timeframe, limit=limit)
    adapter = ft.strategies.wrap(strategy_inst)

    # Simulate basic signal generation and ROI / SL check
    signals = []
    for i in range(20, len(df)):
        sub_df = df.iloc[: i + 1]
        sig = adapter.get_signal(sub_df, pair=symbol)
        if sig.side:
            signals.append({
                "bar_index": i,
                "timestamp": df.iloc[i]["timestamp"].isoformat() if hasattr(df.iloc[i]["timestamp"], "isoformat") else str(df.iloc[i]["timestamp"]),
                "price": float(df.iloc[i]["close"]),
                "side": sig.side,
                "sl": sig.sl,
                "tp": sig.tp,
                "rr_ratio": sig.rr_ratio,
            })

    return jsonify({
        "status": "OK",
        "strategy": strat_name,
        "symbol": symbol,
        "timeframe": timeframe,
        "candles_analyzed": len(df),
        "signals_generated_count": len(signals),
        "sample_signals": signals[:10],
        "roi_table": strategy_inst.minimal_roi,
        "stoploss": strategy_inst.stoploss,
    }), 200
