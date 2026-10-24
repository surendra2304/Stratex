"""
Stratex/api/advanced_risk_routes.py — Stratex Advanced Risk Management API (Flask Blueprint).

Exposes the AdvancedRiskManager engine via Flask routes matching Stratex's architecture.
"""
from flask import Blueprint, jsonify, request

from execution.advanced_risk_management import AdvancedRiskManager, Position

risk_bp = Blueprint("advanced_risk", __name__, url_prefix="/api/risk")

_risk_manager = AdvancedRiskManager()


@risk_bp.route("/var", methods=["POST"])
def calculate_var():
    """Calculate Value at Risk."""
    data = request.get_json() or {}
    positions = [
        Position(
            symbol=p["symbol"],
            quantity=p["quantity"],
            entry_price=p["entry_price"],
            current_price=p.get("current_price", p["entry_price"]),
            side=p.get("side", "long"),
        )
        for p in data.get("positions", [])
    ]
    result = _risk_manager.calculate_var(
        positions,
        confidence=float(data.get("confidence", 0.95)),
        time_horizon=int(data.get("time_horizon", 1)),
    )
    return jsonify(result)


@risk_bp.route("/portfolio", methods=["POST"])
def portfolio_risk():
    """Calculate portfolio risk metrics."""
    data = request.get_json() or {}
    positions = [
        Position(
            symbol=p["symbol"],
            quantity=p["quantity"],
            entry_price=p["entry_price"],
            side=p.get("side", "long"),
        )
        for p in data.get("positions", [])
    ]
    result = _risk_manager.calculate_portfolio_risk(
        positions,
        data.get("returns"),
    )
    return jsonify(result.__dict__)


@risk_bp.route("/drawdown", methods=["POST"])
def check_drawdown():
    """Check drawdown limits."""
    data = request.get_json() or {}
    positions = [
        Position(
            symbol=p["symbol"],
            quantity=p["quantity"],
            entry_price=p["entry_price"],
            side=p.get("side", "long"),
        )
        for p in data.get("positions", [])
    ]
    result = _risk_manager.check_drawdown_limits(
        positions,
        max_drawdown=float(data.get("max_drawdown", 0.20)),
        warning_drawdown=float(data.get("warning_drawdown", 0.15)),
    )
    return jsonify(result)


@risk_bp.route("/kelly", methods=["POST"])
def kelly_positions():
    """Calculate Kelly positions."""
    data = request.get_json() or {}
    result = _risk_manager.calculate_kelly_positions(
        data.get("trades", []),
        data.get("portfolio_value"),
    )
    return jsonify([r.__dict__ for r in result])


@risk_bp.route("/decompose", methods=["POST"])
def decompose_risk():
    """Decompose risk by factor."""
    data = request.get_json() or {}
    positions = [
        Position(
            symbol=p["symbol"],
            quantity=p["quantity"],
            entry_price=p["entry_price"],
            side=p.get("side", "long"),
        )
        for p in data.get("positions", [])
    ]
    result = _risk_manager.decompose_risk(
        positions,
        data.get("factor_returns"),
    )
    return jsonify(result)


@risk_bp.route("/stress", methods=["POST"])
def stress_test():
    """Stress test portfolio."""
    data = request.get_json() or {}
    positions = [
        Position(
            symbol=p["symbol"],
            quantity=p["quantity"],
            entry_price=p["entry_price"],
            side=p.get("side", "long"),
        )
        for p in data.get("positions", [])
    ]
    results = _risk_manager.stress_test(
        positions,
        data.get("scenarios"),
    )
    return jsonify([r.__dict__ for r in results])


@risk_bp.route("/correlation", methods=["POST"])
def analyze_correlations():
    """Analyze correlations."""
    data = request.get_json() or {}
    result = _risk_manager.analyze_correlations(
        data.get("returns_by_symbol", {}),
    )
    return jsonify({
        "pairs": result.pairs,
        "diversification_ratio": result.diversification_ratio,
        "concentration_risk": result.concentration_risk,
        "hedge_candidates": result.hedge_candidates,
    })


@risk_bp.route("/tail", methods=["POST"])
def assess_tail_risk():
    """Assess tail risk."""
    data = request.get_json() or {}
    positions = [
        Position(
            symbol=p["symbol"],
            quantity=p["quantity"],
            entry_price=p["entry_price"],
            side=p.get("side", "long"),
        )
        for p in data.get("positions", [])
    ]
    result = _risk_manager.assess_tail_risk(
        positions,
        data.get("returns"),
    )
    return jsonify(result.__dict__)
