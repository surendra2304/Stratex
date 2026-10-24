"""
Stratex/api/multi_venue_execution_routes.py — Stratex Multi-Venue Execution API (Flask Blueprint).

Exposes the MultiVenueExecutionEngine via Flask routes matching Stratex's architecture.
"""
from flask import Blueprint, jsonify, request

from execution.multi_venue_execution import (
    MultiVenueExecutionEngine, OrderRequest,
)

multi_venue_bp = Blueprint("multi_venue", __name__, url_prefix="/api/execution")

_engine = MultiVenueExecutionEngine()


@multi_venue_bp.route("/venues/register", methods=["POST"])
def register_venue():
    """Register a trading venue."""
    data = request.get_json() or {}
    venue = _engine.register_venue(
        name=data["name"],
        venue_type=data["venue_type"],
        fee_bps=data.get("fee_bps", 5.0),
        slippage_bps=data.get("slippage_bps", 2.0),
        liquidity_score=data.get("liquidity_score", 0.5),
        latency_ms=data.get("latency_ms", 10.0),
    )
    return jsonify({"venue_id": venue.venue_id, "name": venue.name, "type": venue.venue_type})


@multi_venue_bp.route("/liquidity", methods=["POST"])
def update_liquidity():
    """Update venue liquidity."""
    data = request.get_json() or {}
    liq = _engine.update_liquidity(
        venue_id=data["venue_id"],
        symbol=data["symbol"],
        bid_price=data["bid_price"],
        ask_price=data["ask_price"],
        bid_size=data["bid_size"],
        ask_size=data["ask_size"],
    )
    return jsonify({"venue_id": liq.venue_id, "symbol": liq.symbol, "spread_bps": liq.spread_bps})


@multi_venue_bp.route("/liquidity/<symbol>/aggregate", methods=["GET"])
def aggregate_liquidity(symbol):
    """Aggregate liquidity across venues."""
    agg = _engine.aggregate_liquidity(symbol)
    return jsonify({
        "symbol": agg.symbol,
        "bid_price": agg.bid_price,
        "ask_price": agg.ask_price,
        "spread_bps": agg.spread_bps,
        "total_venues": agg.total_venues,
        "venues": agg.venues,
    })


@multi_venue_bp.route("/route", methods=["POST"])
def route_order():
    """Route an order across venues."""
    data = request.get_json() or {}
    order = OrderRequest(
        order_id=data.get("order_id", ""),
        symbol=data["symbol"],
        side=data["side"],
        quantity=data["quantity"],
        order_type=data.get("order_type", "market"),
        limit_price=data.get("limit_price", 0.0),
        max_slippage_bps=data.get("max_slippage_bps", 10.0),
        urgency=data.get("urgency", "normal"),
    )

    plan = _engine.route_order(order)
    return jsonify({
        "plan_id": plan.plan_id,
        "symbol": plan.symbol,
        "venues_used": plan.venues_used,
        "slices": plan.slices,
        "estimated_total_cost": plan.estimated_total_cost,
        "estimated_slippage": plan.estimated_slippage,
        "liquidity_utilized": plan.liquidity_utilized,
    })


@multi_venue_bp.route("/plans/<plan_id>/execute", methods=["POST"])
def execute_plan(plan_id):
    """Execute an execution plan."""
    result = _engine.execute_plan(plan_id)
    return jsonify({
        "plan_id": result.plan_id,
        "filled_quantity": result.filled_quantity,
        "average_fill_price": result.average_fill_price,
        "total_fees": result.total_fees,
        "total_slippage": result.total_slippage,
        "venues_executed": result.venues_executed,
        "status": result.status,
    })


@multi_venue_bp.route("/report", methods=["GET"])
def get_report():
    """Get multi-venue execution report."""
    return jsonify(_engine.get_report())
