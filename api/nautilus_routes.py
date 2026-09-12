"""api/nautilus_routes.py

REST API Blueprint exposing NautilusTrader Event-Driven Endpoints:
- GET /api/v1/nautilus/status
- POST /api/v1/nautilus/bars/aggregate
- POST /api/v1/nautilus/orders/bracket
- POST /api/v1/nautilus/risk/check
- POST /api/v1/nautilus/simulate
"""

from __future__ import annotations

import time
from datetime import datetime, timezone
from dataclasses import asdict
from flask import Blueprint, jsonify, request

from stratex_nautilus_adapter import (
    nt,
    TradeTick,
    BarType,
    OrderSide,
    OrderType,
)

nautilus_bp = Blueprint("nautilus_bp", __name__, url_prefix="/api/v1/nautilus")


@nautilus_bp.route("/status", methods=["GET"])
def get_status():
    """Returns overall Nautilus engine status and telemetry."""
    return jsonify({
        "status": "OK",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "data": nt.get_status(),
    }), 200


@nautilus_bp.route("/bars/aggregate", methods=["POST"])
def aggregate_bars():
    """Aggregates a stream of raw trade ticks into completed bars."""
    payload = request.get_json(force=True, silent=True) or {}
    ticks_data = payload.get("ticks", [])
    bar_type_str = payload.get("bar_type", "TICK").upper()
    step = float(payload.get("step", 10.0))

    ticks: list[TradeTick] = []
    for t in ticks_data:
        ticks.append(
            TradeTick(
                symbol=str(t.get("symbol", "BTCUSDT")).upper(),
                price=float(t.get("price", 0.0)),
                size=float(t.get("size", 1.0)),
                ts_event=int(t.get("ts_event", time.time_ns())),
                side=OrderSide(t.get("side", "BUY")),
            )
        )

    try:
        bar_type = BarType(bar_type_str)
        bars = nt.aggregate(ticks, bar_type=bar_type, step=step)
        return jsonify({
            "status": "OK",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "data": [asdict(b) for b in bars],
        }), 200
    except Exception as e:
        return jsonify({"status": "ERROR", "message": str(e)}), 400


@nautilus_bp.route("/orders/bracket", methods=["POST"])
def create_bracket():
    """Creates an institutional bracket order (Entry + Take Profit + Stop Loss)."""
    payload = request.get_json(force=True, silent=True) or {}
    symbol = str(payload.get("symbol", "BTCUSDT")).upper()
    side = str(payload.get("side", "BUY")).upper()
    quantity = float(payload.get("quantity", 0.01))
    entry_price = float(payload.get("entry_price", 50000.0))
    take_profit_price = float(payload.get("take_profit_price", 52000.0))
    stop_loss_price = float(payload.get("stop_loss_price", 49000.0))
    entry_type = str(payload.get("entry_type", "LIMIT")).upper()

    try:
        bracket = nt.create_bracket(
            symbol=symbol,
            side=side,
            quantity=quantity,
            entry_price=entry_price,
            take_profit_price=take_profit_price,
            stop_loss_price=stop_loss_price,
            entry_type=entry_type,
        )
        return jsonify({
            "status": "OK",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "data": asdict(bracket),
        }), 201
    except Exception as e:
        return jsonify({"status": "ERROR", "message": str(e)}), 400


@nautilus_bp.route("/risk/check", methods=["POST"])
def check_risk():
    """Evaluates an order against pre-trade institutional risk rules."""
    payload = request.get_json(force=True, silent=True) or {}
    symbol = str(payload.get("symbol", "BTCUSDT")).upper()
    side = str(payload.get("side", "BUY")).upper()
    quantity = float(payload.get("quantity", 0.01))
    price = float(payload.get("price", 50000.0))

    try:
        result = nt.check_risk(symbol=symbol, side=side, quantity=quantity, price=price)
        return jsonify({
            "status": "OK",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "data": asdict(result),
        }), 200
    except Exception as e:
        return jsonify({"status": "ERROR", "message": str(e)}), 400


@nautilus_bp.route("/simulate", methods=["POST"])
def simulate_ticks():
    """Simulates deterministic order matching against incoming ticks."""
    payload = request.get_json(force=True, silent=True) or {}
    ticks_data = payload.get("ticks", [])

    ticks: list[TradeTick] = []
    for t in ticks_data:
        ticks.append(
            TradeTick(
                symbol=str(t.get("symbol", "BTCUSDT")).upper(),
                price=float(t.get("price", 0.0)),
                size=float(t.get("size", 1.0)),
                ts_event=int(t.get("ts_event", time.time_ns())),
                side=OrderSide(t.get("side", "BUY")),
            )
        )

    try:
        fills = nt.simulate(ticks)
        return jsonify({
            "status": "OK",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "data": {
                "fills": [asdict(f) for f in fills],
                "fills_count": len(fills),
            },
        }), 200
    except Exception as e:
        return jsonify({"status": "ERROR", "message": str(e)}), 400
