"""
Stratex/api/live_execution_routes.py — Stratex Live Execution API (Flask Blueprint).

Exposes the LiveExecutionEngine via Flask routes matching Stratex's architecture.
"""
from flask import Blueprint, jsonify, request

from execution.live_execution_engine import (
    LiveExecutionEngine, OrderRequest,
)

live_execution_bp = Blueprint("live_execution", __name__, url_prefix="/api/live")

_engine = LiveExecutionEngine()
_engine.set_account_balance(10000.0)  # Default test balance


@live_execution_bp.route("/exchange/connect", methods=["POST"])
def connect_exchange():
    """Connect to an exchange."""
    data = request.get_json() or {}
    exchange = _engine.connect_exchange(
        exchange_id=data.get("exchange_id", "binance"),
        api_key=data.get("api_key", ""),
        api_secret=data.get("api_secret", ""),
        testnet=data.get("testnet", True),
    )
    return jsonify(exchange.__dict__)


@live_execution_bp.route("/exchange/<exchange_id>/disconnect", methods=["POST"])
def disconnect_exchange(exchange_id):
    """Disconnect from an exchange."""
    success = _engine.disconnect_exchange(exchange_id)
    return jsonify({"status": "disconnected" if success else "not_connected"})


@live_execution_bp.route("/exchanges", methods=["GET"])
def get_connected_exchanges():
    """Get connected exchanges."""
    return jsonify([e.__dict__ for e in _engine.get_connected_exchanges()])


@live_execution_bp.route("/order", methods=["POST"])
def place_order():
    """Place a live order."""
    data = request.get_json() or {}
    order = _engine.place_order(
        exchange_id=data.get("exchange_id", "binance"),
        order=OrderRequest(
            symbol=data["symbol"],
            side=data["side"],
            type=data.get("type", "market"),
            quantity=data["quantity"],
            price=data.get("price", 0.0),
            leverage=data.get("leverage", 1),
            stop_loss=data.get("stop_loss", 0.0),
            take_profit=data.get("take_profit", 0.0),
        ),
    )
    return jsonify(order.__dict__)


@live_execution_bp.route("/orders/<order_id>/cancel", methods=["POST"])
def cancel_order(order_id):
    """Cancel a live order."""
    success = _engine.cancel_order(order_id)
    return jsonify({"status": "cancelled" if success else "failed"})


@live_execution_bp.route("/orders/open", methods=["GET"])
def get_open_orders():
    """Get open orders."""
    return jsonify([o.__dict__ for o in _engine.get_open_orders()])


@live_execution_bp.route("/orders/history", methods=["GET"])
def get_order_history():
    """Get order history."""
    limit = int(request.args.get("limit", 50))
    return jsonify([o.__dict__ for o in _engine.get_order_history(limit)])


@live_execution_bp.route("/positions", methods=["GET"])
def get_positions():
    """Get open positions."""
    return jsonify([p.__dict__ for p in _engine.get_positions()])


@live_execution_bp.route("/positions/<symbol>/close", methods=["POST"])
def close_position(symbol):
    """Close a position."""
    success = _engine.close_position(symbol)
    return jsonify({"status": "closed" if success else "not_found"})


@live_execution_bp.route("/account", methods=["GET"])
def get_account():
    """Get account state."""
    return jsonify(_engine.get_account().__dict__)


@live_execution_bp.route("/ticker", methods=["GET"])
def get_ticker():
    """Get ticker for a symbol."""
    symbol = request.args.get("symbol", "BTC/USDT")
    return jsonify(_engine.get_ticker(symbol))


@live_execution_bp.route("/risk/check", methods=["POST"])
def check_risk():
    """Check risk for an order."""
    data = request.get_json() or {}
    order = OrderRequest(
        symbol=data["symbol"],
        side=data["side"],
        type=data.get("type", "market"),
        quantity=data["quantity"],
        price=data.get("price", 0.0),
        leverage=data.get("leverage", 1),
    )
    risk = _engine.check_risk(order)
    return jsonify(risk.__dict__)
