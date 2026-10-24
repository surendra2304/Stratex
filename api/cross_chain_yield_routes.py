"""
Stratex/api/cross_chain_yield_routes.py — Stratex Cross-Chain Yield API (Flask Blueprint).

Exposes the CrossChainYieldOptimizer via Flask routes matching Stratex's architecture.
"""
from flask import Blueprint, jsonify, request

from execution.cross_chain_yield_optimizer import (
    CrossChainYieldOptimizer,
)

yield_optimizer_bp = Blueprint("yield_optimizer", __name__, url_prefix="/api/yield")

_optimizer = CrossChainYieldOptimizer()


@yield_optimizer_bp.route("/chains/register", methods=["POST"])
def register_chain():
    """Register a blockchain chain."""
    data = request.get_json() or {}
    chain = _optimizer.register_chain(
        chain_id=data["chain_id"],
        name=data["name"],
        native_token=data["native_token"],
        gas_price_gwei=data.get("gas_price_gwei", 1.0),
    )
    return jsonify({"chain_id": chain.chain_id, "name": chain.name, "status": "registered"})


@yield_optimizer_bp.route("/opportunities", methods=["POST"])
def add_yield_opportunity():
    """Add a yield opportunity."""
    data = request.get_json() or {}
    opp = _optimizer.add_yield_opportunity(
        chain_id=data["chain_id"],
        protocol=data["protocol"],
        pool_name=data["pool_name"],
        token_pair=data["token_pair"],
        apy=data["apy"],
        tvl_usd=data["tvl_usd"],
        risk_score=data.get("risk_score", 0.5),
    )
    return jsonify({"opportunity_id": opp.opportunity_id, "protocol": opp.protocol, "apy": opp.apy})


@yield_optimizer_bp.route("/bridges", methods=["POST"])
def add_bridge():
    """Add a cross-chain bridge."""
    data = request.get_json() or {}
    bridge = _optimizer.add_bridge(
        name=data["name"],
        source_chain=data["source_chain"],
        target_chain=data["target_chain"],
        fee_pct=data.get("fee_pct", 0.0),
        security_score=data.get("security_score", 0.5),
    )
    return jsonify({"bridge_id": bridge.bridge_id, "name": bridge.name, "status": "added"})


@yield_optimizer_bp.route("/arbitrage", methods=["GET"])
def detect_arbitrage():
    """Detect cross-chain arbitrage opportunities."""
    token = request.args.get("token", "USDC")
    arbitrages = _optimizer.detect_arbitrage(token)
    return jsonify({
        "opportunities": [
            {
                "token": a.token,
                "buy_chain": a.buy_chain,
                "sell_chain": a.sell_chain,
                "price_diff_pct": a.price_diff_pct,
                "net_profit_usd": a.net_profit_usd,
                "risk_level": a.risk_level,
            }
            for a in arbitrages
        ],
        "total_found": len(arbitrages),
    })


@yield_optimizer_bp.route("/optimize", methods=["POST"])
def optimize_allocation():
    """Optimize yield allocation."""
    data = request.get_json() or {}
    strategy = _optimizer.optimize_allocation(
        capital_usd=data["capital_usd"],
        min_apy=data.get("min_apy", 5.0),
        max_risk=data.get("max_risk", 0.8),
    )
    return jsonify({
        "strategy_id": strategy.strategy_id,
        "expected_apy": strategy.expected_apy,
        "expected_risk": strategy.expected_risk,
        "net_return_usd": strategy.net_return_usd,
        "roi": strategy.roi,
        "diversification_score": strategy.diversification_score,
        "allocations": strategy.allocations,
    })


@yield_optimizer_bp.route("/strategies/<strategy_id>/risk", methods=["GET"])
def assess_risk(strategy_id):
    """Assess risk for a strategy."""
    risk = _optimizer.assess_risk(strategy_id)
    return jsonify(risk.__dict__)


@yield_optimizer_bp.route("/report", methods=["GET"])
def get_report():
    """Get yield optimizer report."""
    return jsonify(_optimizer.get_report())
