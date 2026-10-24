"""
Stratex/api/portfolio_risk_routes.py — Stratex Portfolio Risk API (Flask Blueprint).

Exposes the PortfolioRiskAnalytics engine via Flask routes matching Stratex's architecture.
"""
from flask import Blueprint, jsonify, request

from execution.portfolio_risk_analytics import (
    PortfolioRiskAnalytics,
)

risk_bp = Blueprint("portfolio_risk", __name__, url_prefix="/api/risk")

_analytics = PortfolioRiskAnalytics()


@risk_bp.route("/portfolios", methods=["POST"])
def create_portfolio():
    """Create a portfolio."""
    data = request.get_json() or {}
    portfolio = _analytics.create_portfolio(
        name=data["name"],
        cash_balance=data.get("cash_balance", 0.0),
        risk_profile=data.get("risk_profile", "balanced"),
    )
    return jsonify({"portfolio_id": portfolio.portfolio_id, "name": portfolio.name})


@risk_bp.route("/portfolios/<portfolio_id>/positions", methods=["POST"])
def add_position(portfolio_id):
    """Add a position."""
    data = request.get_json() or {}
    position = _analytics.add_position(
        portfolio_id=portfolio_id,
        symbol=data["symbol"],
        quantity=data["quantity"],
        avg_price=data["avg_price"],
    )
    return jsonify({"symbol": position.symbol, "quantity": position.quantity, "market_value": position.market_value})


@risk_bp.route("/portfolios/<portfolio_id>/var", methods=["POST"])
def calculate_var(portfolio_id):
    """Calculate VaR."""
    data = request.get_json() or {}
    result = _analytics.calculate_var(
        portfolio_id=portfolio_id,
        method=data.get("method", "historical"),
        confidence=data.get("confidence", 0.95),
        horizon=data.get("horizon", "1d"),
    )
    return jsonify({
        "var_95": result.var_95,
        "var_99": result.var_99,
        "cvar_95": result.cvar_95,
        "portfolio_value": result.portfolio_value,
        "method": result.var_method,
    })


@risk_bp.route("/portfolios/<portfolio_id>/stress-test", methods=["POST"])
def stress_test(portfolio_id):
    """Run stress test."""
    data = request.get_json() or {}
    result = _analytics.stress_test(
        portfolio_id=portfolio_id,
        scenario_name=data["scenario_name"],
        market_shock_pct=data.get("market_shock_pct", -20.0),
    )
    return jsonify({
        "scenario": result.scenario_name,
        "loss_pct": result.loss_pct,
        "loss_usd": result.loss_usd,
        "recovery_estimate": result.recovery_estimate,
    })


@risk_bp.route("/portfolios/<portfolio_id>/monte-carlo", methods=["POST"])
def monte_carlo(portfolio_id):
    """Run Monte Carlo simulation."""
    data = request.get_json() or {}
    result = _analytics.monte_carlo_simulation(
        portfolio_id=portfolio_id,
        simulations=data.get("simulations", 10000),
    )
    return jsonify({
        "avg_final_value": result.avg_final_value,
        "std_dev": result.std_dev,
        "prob_loss": result.prob_loss,
        "prob_5pct_loss": result.prob_5pct_loss,
        "prob_10pct_gain": result.prob_10pct_gain,
    })


@risk_bp.route("/report", methods=["GET"])
def get_report():
    """Get portfolio risk analytics report."""
    return jsonify(_analytics.get_report())
