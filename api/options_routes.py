"""
Stratex/api/options_routes.py — Stratex Options (Flask Blueprint).
"""
from flask import Blueprint, jsonify, request

from execution.options_pricing_engine import OptionsPricingEngine

options_bp = Blueprint("options", __name__, url_prefix="/api/v1/trading/options")

_options_engine = OptionsPricingEngine()


@options_bp.route("/contracts", methods=["POST"])
def create_contract():
    """Create an options contract."""
    data = request.get_json() or {}
    contract = _options_engine.create_contract(
        symbol=data["symbol"],
        strike_price=data["strike_price"],
        expiry_days=data["expiry_days"],
        option_type=data.get("option_type", "call"),
        underlying_price=data.get("underlying_price", 100.0),
        market_price=data.get("market_price", 0.0),
    )
    return jsonify({"contract_id": contract.contract_id, "symbol": contract.symbol, "iv": contract.iv, "price": contract.market_price})


@options_bp.route("/chains/<symbol>", methods=["POST"])
def build_chain(symbol):
    """Build an option chain."""
    data = request.get_json() or {}
    chain = _options_engine.build_chain(
        symbol=symbol,
        expiry_days=data.get("expiry_days", 30),
        underlying_price=data.get("underlying_price", 100.0),
    )
    return jsonify({"symbol": chain.symbol, "calls": len(chain.calls), "puts": len(chain.puts), "pc_ratio": chain.pc_ratio, "max_pain": chain.max_pain})


@options_bp.route("/surface/<symbol>", methods=["POST"])
def build_surface(symbol):
    """Build a volatility surface."""
    data = request.get_json() or {}
    surface = _options_engine.build_surface(
        symbol=symbol,
        spot_price=data.get("spot_price", 100.0),
    )
    return jsonify({"symbol": surface.symbol, "avg_iv": surface.avg_iv, "skew": surface.skew, "term_structure": surface.term_structure})


@options_bp.route("/strategies", methods=["POST"])
def suggest_strategy():
    """Suggest an options strategy."""
    data = request.get_json() or {}
    strategy = _options_engine.suggest_strategy(
        symbol=data["symbol"],
        outlook=data["outlook"],
        risk_tolerance=data.get("risk_tolerance", "moderate"),
    )
    return jsonify({"strategy_id": strategy.strategy_id, "name": strategy.name, "max_profit": strategy.max_profit, "probability_of_profit": strategy.probability_of_profit})


@options_bp.route("/report", methods=["GET"])
def get_report():
    """Get options pricing engine report."""
    return jsonify(_options_engine.get_report())
