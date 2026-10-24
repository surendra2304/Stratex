"""
Stratex/api/market_making_routes.py — Stratex Market Making (Flask Blueprint).

Exposes the AlgorithmicMarketMaker engine via Flask routes matching Stratex's architecture.
"""
from flask import Blueprint, jsonify, request

from execution.algorithmic_market_making import AlgorithmicMarketMaker

mk_bp = Blueprint("market_making", __name__, url_prefix="/api/v1/trading/market-making")

_market_maker = AlgorithmicMarketMaker()


@mk_bp.route("/config", methods=["POST"])
def configure():
    """Configure market making."""
    data = request.get_json() or {}
    config = _market_maker.configure(
        symbol=data["symbol"],
        spread_pct=data.get("spread_pct", 0.1),
        max_inventory=data.get("max_inventory", 1000.0),
        quote_frequency_ms=data.get("quote_frequency_ms", 100),
    )
    return jsonify({"config_id": config.config_id, "symbol": config.symbol, "enabled": config.enabled})


@mk_bp.route("/quote", methods=["POST"])
def generate_quote():
    """Generate a quote."""
    data = request.get_json() or {}
    quote = _market_maker.generate_quote(
        symbol=data["symbol"],
        mid_price=data["mid_price"],
        spread_bps=data.get("spread_bps", 10.0),
    )
    return jsonify({
        "symbol": quote.symbol,
        "bid": round(quote.bid, 2),
        "ask": round(quote.ask, 2),
        "spread": round(quote.spread, 2),
        "mid": round(quote.mid, 2),
    })


@mk_bp.route("/inventory", methods=["POST"])
def update_inventory():
    """Update inventory after fill."""
    data = request.get_json() or {}
    inv = _market_maker.update_inventory(
        symbol=data["symbol"],
        quantity=data["quantity"],
        price=data["price"],
    )
    return jsonify({
        "symbol": inv.symbol,
        "quantity": inv.quantity,
        "pnl": inv.pnl,
        "realized_pnl": inv.realized_pnl,
        "unrealized_pnl": inv.unrealized_pnl,
    })


@mk_bp.route("/adverse-selection", methods=["POST"])
def detect_adverse_selection():
    """Detect adverse selection."""
    data = request.get_json() or {}
    event = _market_maker.detect_adverse_selection(
        symbol=data["symbol"],
        trigger=data["trigger"],
        description=data.get("description", ""),
    )
    return jsonify({"event_id": event.event_id, "symbol": event.symbol, "severity": event.severity, "quote_suspended": event.quote_suspended})


@mk_bp.route("/hedge", methods=["POST"])
def execute_hedge():
    """Execute a hedge."""
    data = request.get_json() or {}
    hedge = _market_maker.execute_hedge(
        symbol=data["symbol"],
        quantity=data["quantity"],
        hedge_type=data.get("hedge_type", "offset"),
    )
    return jsonify({"hedge_id": hedge.hedge_id, "side": hedge.side, "quantity": hedge.quantity, "executed": hedge.executed})


@mk_bp.route("/report", methods=["GET"])
def get_report():
    """Get market making report."""
    return jsonify(_market_maker.get_report())
