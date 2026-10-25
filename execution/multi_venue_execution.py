"""
Stratex/execution/multi_venue_execution.py — Stratex Multi-Venue Execution Routing Engine.

Routes trades across multiple venues (CEX, DEX, dark pools, ECNs) for optimal
execution. Supports smart order routing, liquidity aggregation, venue
selection, slippage minimization, and best-bid-offer (BBO) optimization.
"""
from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass, field
from typing import Any

from fastapi import APIRouter, HTTPException

router = APIRouter()


@dataclass
class VenueConfig:
    """Configuration for a trading venue."""
    venue_id: str
    name: str
    venue_type: str  # CEX, DEX, dark_pool, ECN, ATS
    fee_bps: float = 5.0
    slippage_bps: float = 2.0
    min_order_size: float = 0.001
    max_order_size: float = 1e9
    liquidity_score: float = 0.5
    latency_ms: float = 10.0
    supported_pairs: list[str] = field(default_factory=list)
    active: bool = True


@dataclass
class OrderRequest:
    """An order to route."""
    order_id: str
    symbol: str
    side: str  # buy, sell
    quantity: float
    order_type: str = "market"  # market, limit, stop, iceberg
    limit_price: float = 0.0
    stop_price: float = 0.0
    venue_preferences: list[str] = field(default_factory=list)
    max_slippage_bps: float = 10.0
    urgency: str = "normal"  # passive, normal, aggressive
    created_at: float = 0.0


@dataclass
class ExecutionPlan:
    """A multi-venue execution plan."""
    plan_id: str
    order_id: str
    symbol: str
    side: str
    total_quantity: float
    slices: list[dict] = field(default_factory=list)  # [{venue, quantity, price, estimated_fee}]
    estimated_fill_time_ms: float = 0.0
    estimated_total_cost: float = 0.0
    estimated_slippage: float = 0.0
    venues_used: int = 0
    liquidity_utilized: float = 0.0
    created_at: float = 0.0


@dataclass
class VenueLiquidity:
    """Liquidity snapshot for a venue."""
    venue_id: str
    symbol: str
    bid_price: float = 0.0
    ask_price: float = 0.0
    bid_size: float = 0.0
    ask_size: float = 0.0
    spread_bps: float = 0.0
    depth: float = 0.0
    timestamp: float = 0.0


@dataclass
class LiquidityAggregate:
    """Aggregated liquidity across venues."""
    symbol: str
    bid_price: float = 0.0
    ask_price: float = 0.0
    bid_size: float = 0.0
    ask_size: float = 0.0
    spread_bps: float = 0.0
    total_venues: int = 0
    venues: list[str] = field(default_factory=list)


@dataclass
class ExecutionResult:
    """Result of execution."""
    plan_id: str
    order_id: str
    filled_quantity: float = 0.0
    average_fill_price: float = 0.0
    total_fees: float = 0.0
    total_slippage: float = 0.0
    venues_executed: list[str] = field(default_factory=list)
    fills: list[dict] = field(default_factory=list)
    status: str = "pending"
    completed_at: float = 0.0


@dataclass
class VenuePerformance:
    """Performance metrics for a venue."""
    venue_id: str
    total_orders: int = 0
    filled_orders: int = 0
    avg_latency_ms: float = 0.0
    avg_slippage_bps: float = 0.0
    avg_fee_bps: float = 0.0
    fill_rate: float = 0.0
    smart_route_score: float = 0.0


class MultiVenueExecutionEngine:
    """Multi-venue execution routing engine."""

    def __init__(self):
        self._venues: dict[str, VenueConfig] = {}
        self._liquidity: dict[str, VenueLiquidity] = {}
        self._plans: dict[str, ExecutionPlan] = {}
        self._results: dict[str, ExecutionResult] = {}
        self._venue_performance: dict[str, VenuePerformance] = {}

        # Register default venues
        self._register_default_venues()

    def _register_default_venues(self):
        """Register default trading venues."""
        venues = [
            ("binance", "Binance", "CEX", 5.0, 1.5, 1e9, 0.9, 15.0),
            ("coinbase", "Coinbase Pro", "CEX", 4.0, 2.0, 5e8, 0.85, 20.0),
            ("kraken", "Kraken", "CEX", 4.5, 2.5, 3e8, 0.8, 25.0),
            ("uniswap_v3", "Uniswap V3", "DEX", 3.0, 5.0, 1e8, 0.7, 50.0),
            ("sushiswap", "SushiSwap", "DEX", 3.5, 6.0, 5e7, 0.65, 60.0),
            ("dark_pool", "Dark Pool", "dark_pool", 1.0, 0.5, 1e9, 0.75, 30.0),
            ("ecn", "ECN", "ECN", 2.0, 1.0, 5e8, 0.8, 10.0),
        ]

        for vid, name, vtype, fee, slip, min_sz, max_sz, liq, lat in venues:
            self._venues[vid] = VenueConfig(
                venue_id=vid,
                name=name,
                venue_type=vtype,
                fee_bps=fee,
                slippage_bps=slip,
                min_order_size=min_sz,
                max_order_size=max_sz,
                liquidity_score=liq,
                latency_ms=lat,
            )
            self._venue_performance[vid] = VenuePerformance(venue_id=vid)

    def register_venue(
        self,
        name: str,
        venue_type: str,
        fee_bps: float = 5.0,
        slippage_bps: float = 2.0,
        liquidity_score: float = 0.5,
        latency_ms: float = 10.0,
    ) -> VenueConfig:
        """Register a trading venue."""
        venue = VenueConfig(
            venue_id=hashlib.md5(
                f"venue:{name}:{time.time()}".encode()
            ).hexdigest()[:12],
            name=name,
            venue_type=venue_type,
            fee_bps=fee_bps,
            slippage_bps=slippage_bps,
            liquidity_score=liquidity_score,
            latency_ms=latency_ms,
        )
        self._venues[venue.venue_id] = venue
        self._venue_performance[venue.venue_id] = VenuePerformance(venue_id=venue.venue_id)
        return venue

    def update_liquidity(
        self,
        venue_id: str,
        symbol: str,
        bid_price: float,
        ask_price: float,
        bid_size: float,
        ask_size: float,
    ) -> VenueLiquidity:
        """Update venue liquidity snapshot."""
        venue = self._venues.get(venue_id)
        if not venue:
            raise HTTPException(404, f"Venue {venue_id} not found")

        liq = VenueLiquidity(
            venue_id=venue_id,
            symbol=symbol,
            bid_price=bid_price,
            ask_price=ask_price,
            bid_size=bid_size,
            ask_size=ask_size,
            spread_bps=(ask_price - bid_price) / bid_price * 10000 if bid_price > 0 else 0,
            depth=bid_size + ask_size,
            timestamp=time.time(),
        )

        self._liquidity[f"{venue_id}:{symbol}"] = liq
        return liq

    def aggregate_liquidity(self, symbol: str) -> LiquidityAggregate:
        """Aggregate liquidity across venues for a symbol."""
        liqs = [v for k, v in self._liquidity.items() if k.endswith(f":{symbol}")]

        if not liqs:
            return LiquidityAggregate(symbol=symbol)

        # Best bid (highest) and best ask (lowest)
        best_bid = max(liqs, key=lambda l: l.bid_price)
        best_ask = min(liqs, key=lambda l: l.ask_price)

        total_bid_size = sum(l.bid_size for l in liqs)
        total_ask_size = sum(l.ask_size for l in liqs)

        return LiquidityAggregate(
            symbol=symbol,
            bid_price=best_bid.bid_price,
            ask_price=best_ask.ask_price,
            bid_size=total_bid_size,
            ask_size=total_ask_size,
            spread_bps=(best_ask.ask_price - best_bid.bid_price) / best_bid.bid_price * 10000 if best_bid.bid_price > 0 else 0,
            total_venues=len(liqs),
            venues=[l.venue_id for l in liqs],
        )

    def route_order(self, order: OrderRequest) -> ExecutionPlan:
        """Route an order across venues."""
        start = time.time()

        plan = ExecutionPlan(
            plan_id=hashlib.md5(
                f"plan:{order.order_id}:{time.time()}".encode()
            ).hexdigest()[:12],
            order_id=order.order_id,
            symbol=order.symbol,
            side=order.side,
            total_quantity=order.quantity,
            created_at=time.time(),
        )

        # Get aggregated liquidity
        agg = self.aggregate_liquidity(order.symbol)

        if not agg.venues:
            raise HTTPException(400, "No liquidity available for symbol")

        # Select venues based on preferences, liquidity, fees, slippage
        available_venues = []
        for venue_id in agg.venues:
            venue = self._venues.get(venue_id)
            if not venue or not venue.active:
                continue

            liq = self._liquidity.get(f"{venue_id}:{order.symbol}")
            if not liq:
                continue

            # Score venue
            score = (
                venue.liquidity_score * 0.4 +
                (1 - venue.fee_bps / 100) * 0.2 +
                (1 - venue.slippage_bps / 50) * 0.2 +
                (1 - venue.latency_ms / 100) * 0.2
            )

            available_venues.append({
                "venue_id": venue_id,
                "venue": venue,
                "liquidity": liq,
                "score": score,
            })

        # Sort by score
        available_venues.sort(key=lambda v: -v["score"])

        # Filter by preferences
        if order.venue_preferences:
            preferred = [v for v in available_venues if v["venue_id"] in order.venue_preferences]
            if preferred:
                available_venues = preferred

        # Distribute order across top venues
        remaining = order.quantity
        total_fee = 0.0
        total_slippage = 0.0

        for venue_info in available_venues:
            venue = venue_info["venue"]
            liq = venue_info["liquidity"]

            # Determine slice size (proportional to liquidity)
            venue_share = venue.liquidity_score / sum(v["venue"].liquidity_score for v in available_venues)
            slice_qty = min(remaining, order.quantity * venue_share)

            if slice_qty < venue.min_order_size:
                continue

            # Determine fill price
            if order.side == "buy":
                fill_price = liq.ask_price * (1 + venue.slippage_bps / 10000)
            else:
                fill_price = liq.bid_price * (1 - venue.slippage_bps / 10000)

            # Calculate fee
            fee = slice_qty * fill_price * venue.fee_bps / 10000

            total_fee += fee
            total_slippage += abs(fill_price - (liq.ask_price if order.side == "buy" else liq.bid_price)) / (liq.ask_price if order.side == "buy" else liq.bid_price) * 10000

            plan.slices.append({
                "venue": venue.name,
                "venue_id": venue.venue_id,
                "quantity": slice_qty,
                "price": fill_price,
                "estimated_fee": fee,
            })

            remaining -= slice_qty
            plan.venues_used += 1

            if remaining <= 0:
                break

        # Calculate metrics
        plan.estimated_total_cost = total_fee
        plan.estimated_slippage = total_slippage
        plan.estimated_fill_time_ms = max(
            (v["venue"].latency_ms for v in available_venues[:plan.venues_used]),
            default=0,
        )
        plan.liquidity_utilized = sum(
            s["quantity"] for s in plan.slices
        ) / max(1, order.quantity)

        self._plans[plan.plan_id] = plan
        return plan

    def execute_plan(self, plan_id: str) -> ExecutionResult:
        """Execute an execution plan."""
        plan = self._plans.get(plan_id)
        if not plan:
            raise HTTPException(404, f"Plan {plan_id} not found")

        result = ExecutionResult(
            plan_id=plan_id,
            order_id=plan.order_id,
            filled_quantity=plan.total_quantity,
            average_fill_price=sum(s["price"] * s["quantity"] for s in plan.slices) / max(1, plan.total_quantity),
            total_fees=plan.estimated_total_cost,
            total_slippage=plan.estimated_slippage,
            venues_executed=[s["venue_id"] for s in plan.slices],
            fills=plan.slices,
            status="filled",
            completed_at=time.time(),
        )

        # Update venue performance
        for venue_id in result.venues_executed:
            perf = self._venue_performance.get(venue_id)
            if perf:
                perf.total_orders += 1
                perf.filled_orders += 1
                perf.avg_latency_ms = perf.avg_latency_ms * 0.9 + self._venues[venue_id].latency_ms * 0.1
                perf.avg_slippage_bps = perf.avg_slippage_bps * 0.9 + result.total_slippage * 0.1
                perf.avg_fee_bps = perf.avg_fee_bps * 0.9 + self._venues[venue_id].fee_bps * 0.1
                perf.fill_rate = perf.filled_orders / max(1, perf.total_orders)
                perf.smart_route_score = perf.fill_rate * (1 - perf.avg_slippage_bps / 100)

        self._results[plan_id] = result
        return result

    def get_report(self) -> dict:
        """Get multi-venue execution report."""
        return {
            "total_venues": len(self._venues),
            "total_plans": len(self._plans),
            "total_results": len(self._results),
            "venues": [
                {
                    "name": v.name,
                    "type": v.venue_type,
                    "fee_bps": v.fee_bps,
                    "liquidity_score": v.liquidity_score,
                    "active": v.active,
                }
                for v in self._venues.values()
            ],
            "venue_performance": [
                {
                    "venue": v.venue_id,
                    "fill_rate": v.fill_rate,
                    "avg_slippage_bps": v.avg_slippage_bps,
                    "smart_route_score": v.smart_route_score,
                }
                for v in self._venue_performance.values()
            ],
        }


# Module-level singleton
execution_engine = MultiVenueExecutionEngine()


# ── API Routes ──────────────────────────────────────────────────────────────


@router.post("/api/v1/trading/execution/venues")
async def register_venue(payload: dict):
    """Register a trading venue."""
    venue = execution_engine.register_venue(
        name=payload["name"],
        venue_type=payload["venue_type"],
        fee_bps=payload.get("fee_bps", 5.0),
        slippage_bps=payload.get("slippage_bps", 2.0),
        liquidity_score=payload.get("liquidity_score", 0.5),
        latency_ms=payload.get("latency_ms", 10.0),
    )
    return {"venue_id": venue.venue_id, "name": venue.name, "type": venue.venue_type}


@router.post("/api/v1/trading/execution/liquidity")
async def update_liquidity(payload: dict):
    """Update venue liquidity."""
    liq = execution_engine.update_liquidity(
        venue_id=payload["venue_id"],
        symbol=payload["symbol"],
        bid_price=payload["bid_price"],
        ask_price=payload["ask_price"],
        bid_size=payload["bid_size"],
        ask_size=payload["ask_size"],
    )
    return {"venue_id": liq.venue_id, "symbol": liq.symbol, "spread_bps": liq.spread_bps}


@router.get("/api/v1/trading/execution/liquidity/{symbol}/aggregate")
async def aggregate_liquidity(symbol: str):
    """Aggregate liquidity across venues."""
    agg = execution_engine.aggregate_liquidity(symbol)
    return {
        "symbol": agg.symbol,
        "bid_price": agg.bid_price,
        "ask_price": agg.ask_price,
        "spread_bps": agg.spread_bps,
        "total_venues": agg.total_venues,
        "venues": agg.venues,
    }


@router.post("/api/v1/trading/execution/route")
async def route_order(payload: dict):
    """Route an order across venues."""
    order = OrderRequest(
        order_id=payload.get("order_id", ""),
        symbol=payload["symbol"],
        side=payload["side"],
        quantity=payload["quantity"],
        order_type=payload.get("order_type", "market"),
        limit_price=payload.get("limit_price", 0.0),
        max_slippage_bps=payload.get("max_slippage_bps", 10.0),
        urgency=payload.get("urgency", "normal"),
    )

    plan = execution_engine.route_order(order)
    return {
        "plan_id": plan.plan_id,
        "symbol": plan.symbol,
        "venues_used": plan.venues_used,
        "slices": plan.slices,
        "estimated_total_cost": plan.estimated_total_cost,
        "estimated_slippage": plan.estimated_slippage,
        "liquidity_utilized": plan.liquidity_utilized,
    }


@router.post("/api/v1/trading/execution/plans/{plan_id}/execute")
async def execute_plan(plan_id: str):
    """Execute an execution plan."""
    result = execution_engine.execute_plan(plan_id)
    return {
        "plan_id": result.plan_id,
        "filled_quantity": result.filled_quantity,
        "average_fill_price": result.average_fill_price,
        "total_fees": result.total_fees,
        "total_slippage": result.total_slippage,
        "venues_executed": result.venues_executed,
        "status": result.status,
    }


@router.get("/api/v1/trading/execution/report")
async def get_report():
    """Get multi-venue execution report."""
    return execution_engine.get_report()
