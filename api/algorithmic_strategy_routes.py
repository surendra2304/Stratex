"""
Stratex/api/algorithmic_strategy_routes.py — Stratex Algorithmic Strategy API (Flask Blueprint).

Exposes the AlgorithmicStrategyLibrary via Flask routes matching Stratex's architecture.
"""
from flask import Blueprint, jsonify, request

from execution.algorithmic_strategy_library import (
    AlgorithmicStrategyLibrary,
)

strategy_bp = Blueprint("strategy_library", __name__, url_prefix="/api/strategy")

_library = AlgorithmicStrategyLibrary()


@strategy_bp.route("/register", methods=["POST"])
def register_strategy():
    """Register a trading strategy."""
    data = request.get_json() or {}
    strategy = _library.register_strategy(
        name=data["name"],
        strategy_type=data["strategy_type"],
        symbol=data.get("symbol", ""),
        parameters=data.get("parameters", {}),
        risk_per_trade=data.get("risk_per_trade", 0.01),
    )
    return jsonify({"strategy_id": strategy.strategy_id, "name": strategy.name, "type": strategy.strategy_type})


@strategy_bp.route("/<strategy_id>/backtest", methods=["POST"])
def backtest_strategy(strategy_id):
    """Backtest a trading strategy."""
    data = request.get_json() or {}
    result = _library.backtest_strategy(
        strategy_id=strategy_id,
        symbol=data["symbol"],
        period=data.get("period", "30d"),
        initial_capital=data.get("initial_capital", 10000.0),
    )
    return jsonify({
        "strategy_id": result.strategy_id,
        "total_return_pct": result.total_return_pct,
        "sharpe_ratio": result.sharpe_ratio,
        "sortino_ratio": result.sortino_ratio,
        "win_rate": result.win_rate,
        "drawdown_pct": result.drawdown_pct,
        "profit_factor": result.profit_factor,
        "trades_total": result.trades_total,
        "final_capital": result.final_capital,
    })


@strategy_bp.route("/<strategy_id>/signals/<symbol>", methods=["GET"])
def generate_signal(strategy_id, symbol):
    """Generate a trading signal."""
    signal = _library.generate_signal(strategy_id, symbol)
    return jsonify({
        "signal_id": signal.signal_id,
        "symbol": signal.symbol,
        "direction": signal.direction,
        "entry_price": signal.entry_price,
        "target_price": signal.target_price,
        "stop_loss_price": signal.stop_loss_price,
        "confidence": signal.confidence,
        "risk_reward_ratio": signal.risk_reward_ratio,
    })


@strategy_bp.route("/<strategy_id>/ml-model", methods=["POST"])
def register_ml_model(strategy_id):
    """Register an ML model for a strategy."""
    data = request.get_json() or {}
    model = _library.register_ml_model(
        strategy_id=strategy_id,
        model_type=data["model_type"],
        features=data.get("features", []),
    )
    return jsonify({
        "model_id": model.model_id,
        "model_type": model.model_type,
        "accuracy": model.accuracy,
        "precision": model.precision,
        "recall": model.recall,
        "f1_score": model.f1_score,
    })


@strategy_bp.route("/<strategy_id>/optimize", methods=["POST"])
def optimize_strategy(strategy_id):
    """Optimize strategy parameters."""
    data = request.get_json() or {}
    method = data.get("method", "bayesian")
    optimization = _library.optimize_strategy(strategy_id, method)
    return jsonify({
        "strategy_id": optimization.strategy_id,
        "best_parameters": optimization.best_parameters,
        "best_sharpe": optimization.best_sharpe,
        "best_return_pct": optimization.best_return_pct,
        "best_drawdown_pct": optimization.best_drawdown_pct,
        "parameters_tested": optimization.parameters_tested,
    })


@strategy_bp.route("/report", methods=["GET"])
def get_report():
    """Get strategy library report."""
    return jsonify(_library.get_report())
