"""
Stratex/api/trade_optimizer_routes.py — Stratex Trade Optimizer API (Flask Blueprint).

Exposes the TradeOptimizer engine via Flask routes matching Stratex's architecture.
"""
import json

from flask import Blueprint, jsonify, request

from execution.trade_optimizer import TradeOptimizer

trade_optimizer_bp = Blueprint("trade_optimizer", __name__, url_prefix="/api/optimizer")

_optimizer = TradeOptimizer()


@trade_optimizer_bp.route("/record", methods=["POST"])
def record_trade():
    """Record a trade result for strategy tracking."""
    data = request.get_json() or {}
    sp = _optimizer.record_trade(
        strategy=data.get("strategy", "unknown"),
        pnl=float(data.get("pnl", 0)),
        win=bool(data.get("win", False)),
        risk=float(data.get("risk", 0)),
    )
    return jsonify(sp.__dict__)


@trade_optimizer_bp.route("/performance", methods=["GET"])
def strategy_performance():
    """Get all strategy performances."""
    perf = _optimizer.get_strategy_performance()
    return jsonify([s.__dict__ for s in perf])


@trade_optimizer_bp.route("/allocation", methods=["GET"])
def optimize_allocation():
    """Get optimized portfolio allocation."""
    target_return = float(request.args.get("target_return", 0.1))
    max_risk = float(request.args.get("max_risk", 0.2))
    allocations = _optimizer.optimize_allocation(target_return, max_risk)
    return jsonify([a.__dict__ for a in allocations])


@trade_optimizer_bp.route("/regime", methods=["POST"])
def detect_regime():
    """Detect current market regime."""
    data = request.get_json() or {}
    regime = _optimizer.detect_market_regime(
        prices=data.get("prices", []),
        volumes=data.get("volumes"),
    )
    return jsonify(regime.__dict__)


@trade_optimizer_bp.route("/switch", methods=["POST"])
def switch_strategy():
    """Switch to optimal strategy for current regime."""
    from execution.trade_optimizer import MarketRegime
    data = request.get_json() or {}
    regime = MarketRegime(
        regime=data.get("regime", "sideways"),
        confidence=float(data.get("confidence", 0.5)),
        suggested_strategies=data.get("suggested_strategies", []),
    )
    result = _optimizer.switch_strategy(
        data.get("current_strategy", ""),
        regime,
        _optimizer.get_strategy_performance(),
    )
    return jsonify(result)


@trade_optimizer_bp.route("/risk_parity", methods=["POST"])
def risk_parity():
    """Get risk parity allocation."""
    data = request.get_json() or {}
    allocation = _optimizer.risk_parity_allocation(
        data.get("strategies", []),
        float(data.get("risk_budget", 1.0)),
    )
    return jsonify({"allocation": allocation})


@trade_optimizer_bp.route("/walk_forward", methods=["POST"])
def walk_forward():
    """Run walk-forward optimization."""
    data = request.get_json() or {}
    result = _optimizer.walk_forward_optimize(
        data.get("strategy", ""),
        data.get("returns", []),
        int(data.get("train_window", 50)),
        int(data.get("test_window", 20)),
    )
    return jsonify(result.__dict__)


@trade_optimizer_bp.route("/ensemble", methods=["POST"])
def create_ensemble():
    """Create strategy ensemble."""
    data = request.get_json() or {}
    result = _optimizer.create_ensemble(
        data.get("strategies", []),
        data.get("method", "risk_parity"),
    )
    return jsonify(result.__dict__)


@trade_optimizer_bp.route("/bayesian", methods=["GET"])
def bayesian_select():
    """Get Bayesian strategy selection."""
    selections = _optimizer.bayesian_select(list(_optimizer._strategies.keys()))
    return jsonify([s.__dict__ for s in selections])
