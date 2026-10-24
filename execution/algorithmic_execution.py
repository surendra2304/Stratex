"""
Stratex/execution/algorithmic_execution.py — Algorithmic Trade Execution Engine.

Extends Stratex's capabilities with:
1. Smart Order Routing: find best execution venues
2. Order Splitting: split large orders to minimize market impact
3. Execution Timing: optimize order timing
4. Spread Capture: capture bid-ask spread
5. Iceberg Orders: hide large orders
6. VWAP Execution: execute at volume-weighted average price
7. Implementation Shortfall: minimize execution cost
"""
from __future__ import annotations

import math
import time
from dataclasses import dataclass, field
from typing import Any


@dataclass
class Order:
    """A trade order."""
    order_id: str
    symbol: str
    side: str  # buy, sell
    quantity: float
    order_type: str = "market"  # market, limit, stop, iceberg
    limit_price: float = 0.0
    stop_price: float = 0.0
    visible_quantity: float = 0.0  # For iceberg orders
    status: str = "pending"  # pending, partial, filled, cancelled
    filled_quantity: float = 0.0
    avg_fill_price: float = 0.0
    created_at: float = 0.0


@dataclass
class Venue:
    """An execution venue."""
    venue_id: str
    name: str
    bid_price: float = 0.0
    ask_price: float = 0.0
    bid_size: float = 0.0
    ask_size: float = 0.0
    liquidity_score: float = 0.0
    fee: float = 0.0
    latency: float = 0.0  # milliseconds


@dataclass
class ExecutionPlan:
    """An execution plan for an order."""
    order_id: str
    symbol: str
    total_quantity: float
    child_orders: list[Order] = field(default_factory=list)
    target_vwap: float = 0.0
    estimated_cost: float = 0.0
    estimated_time: float = 0.0
    strategy: str = ""


@dataclass
class ExecutionResult:
    """Result of trade execution."""
    order_id: str
    filled_quantity: float
    avg_price: float
    total_cost: float
    slippage: float
    implementation_shortfall: float
    execution_venue: str
    duration: float
    status: str  # filled, partial, cancelled


@dataclass
class MarketData:
    """Real-time market data."""
    symbol: str
    bid: float
    ask: float
    bid_size: float
    ask_size: float
    last_price: float
    volume: float
    timestamp: float


class AlgorithmicExecution:
    """Algorithmic trade execution engine."""

    def __init__(self):
        self._orders: dict[str, Order] = {}
        self._venues: dict[str, Venue] = {}
        self._execution_plans: dict[str, ExecutionPlan] = {}
        self._results: dict[str, ExecutionResult] = {}

    # ── Smart Order Routing ─────────────────────────────────────────────────

    def route_order(
        self,
        symbol: str,
        side: str,
        quantity: float,
        market_data: dict[str, MarketData],
    ) -> Venue | None:
        """Route an order to the best venue."""
        if not market_data:
            return None

        best_venue = None
        best_score = -1.0

        for venue_id, data in market_data.items():
            # Calculate spread
            spread = data.ask - data.bid
            spread_pct = spread / data.last_price if data.last_price > 0 else 0.0

            # Calculate liquidity score
            liquidity = (data.bid_size + data.ask_size) / 2.0
            liquidity_score = min(1.0, liquidity / 10000.0)

            # Calculate effective cost (spread + fee)
            effective_cost = spread_pct + self._venues.get(venue_id, Venue("", "")).fee / 10000.0

            # Score: lower spread, higher liquidity = better
            score = liquidity_score * 0.7 - effective_cost * 10.0

            if score > best_score:
                best_score = score
                best_venue = data

        return best_venue

    # ── Order Splitting ─────────────────────────────────────────────────────

    def split_order(
        self,
        order_id: str,
        symbol: str,
        side: str,
        quantity: float,
        max_order_size: float = 1000.0,
    ) -> ExecutionPlan:
        """Split a large order into smaller child orders."""
        plan = ExecutionPlan(
            order_id=order_id,
            symbol=symbol,
            total_quantity=quantity,
            strategy="order_splitting",
        )

        # Calculate number of child orders
        num_orders = math.ceil(quantity / max_order_size)
        child_size = quantity / num_orders

        for i in range(num_orders):
            child_order = Order(
                order_id=f"{order_id}_child_{i}",
                symbol=symbol,
                side=side,
                quantity=child_size,
                order_type="limit",
                status="pending",
                created_at=time.time(),
            )
            plan.child_orders.append(child_order)
            self._orders[child_order.order_id] = child_order

        plan.estimated_time = num_orders * 1.0  # 1 second per child order

        self._execution_plans[order_id] = plan
        return plan

    # ── VWAP Execution ─────────────────────────────────────────────────────

    def calculate_vwap(
        self,
        symbol: str,
        trades: list[dict],
    ) -> float:
        """Calculate Volume-Weighted Average Price."""
        if not trades:
            return 0.0

        total_value = 0.0
        total_volume = 0.0

        for trade in trades:
            price = trade.get("price", 0.0)
            volume = trade.get("volume", 0.0)
            total_value += price * volume
            total_volume += volume

        return total_value / max(1.0, total_volume)

    def create_vwap_plan(
        self,
        order_id: str,
        symbol: str,
        side: str,
        quantity: float,
        historical_volume: list[float],
        historical_prices: list[float],
    ) -> ExecutionPlan:
        """Create a VWAP-based execution plan."""
        if not historical_volume or not historical_prices:
            return ExecutionPlan(
                order_id=order_id,
                symbol=symbol,
                total_quantity=quantity,
                strategy="vwap",
            )

        # Calculate target VWAP
        target_vwap = self.calculate_vwap(symbol, [
            {"price": p, "volume": v}
            for p, v in zip(historical_prices, historical_volume)
        ])

        # Create child orders proportional to volume profile
        plan = ExecutionPlan(
            order_id=order_id,
            symbol=symbol,
            total_quantity=quantity,
            target_vwap=target_vwap,
            strategy="vwap",
        )

        # Create child orders weighted by volume
        total_volume = sum(historical_volume)
        for i, (price, vol) in enumerate(zip(historical_prices, historical_volume)):
            child_qty = quantity * (vol / max(1.0, total_volume))

            child_order = Order(
                order_id=f"{order_id}_vwap_{i}",
                symbol=symbol,
                side=side,
                quantity=child_qty,
                order_type="limit",
                limit_price=price,
                status="pending",
                created_at=time.time(),
            )
            plan.child_orders.append(child_order)
            self._orders[child_order.order_id] = child_order

        plan.estimated_cost = abs(target_vwap - historical_prices[0]) * quantity

        self._execution_plans[order_id] = plan
        return plan

    # ── Iceberg Orders ─────────────────────────────────────────────────────

    def create_iceberg_order(
        self,
        order_id: str,
        symbol: str,
        side: str,
        quantity: float,
        visible_quantity: float = 100.0,
    ) -> Order:
        """Create an iceberg order (hidden quantity)."""
        order = Order(
            order_id=order_id,
            symbol=symbol,
            side=side,
            quantity=quantity,
            order_type="iceberg",
            visible_quantity=visible_quantity,
            status="pending",
            created_at=time.time(),
        )

        self._orders[order_id] = order
        return order

    # ── Spread Capture ─────────────────────────────────────────────────────

    def detect_spread_opportunity(
        self,
        symbol: str,
        market_data: MarketData,
        min_spread_pct: float = 0.001,
    ) -> dict[str, Any]:
        """Detect spread capture opportunities."""
        spread = market_data.ask - market_data.bid
        spread_pct = spread / market_data.last_price if market_data.last_price > 0 else 0.0

        if spread_pct < min_spread_pct:
            return {"opportunity": False, "reason": "Spread too narrow"}

        # Calculate potential profit
        min_size = min(market_data.bid_size, market_data.ask_size)
        potential_profit = spread * min_size

        # Check liquidity
        liquidity_ok = min_size > 100

        return {
            "opportunity": True,
            "symbol": symbol,
            "spread": spread,
            "spread_pct": spread_pct,
            "potential_profit": potential_profit,
            "min_order_size": min_size,
            "liquidity_ok": liquidity_ok,
            "recommendation": (
                "Execute market-making strategy" if liquidity_ok else
                "Wait for better liquidity"
            ),
        }

    # ── Execution Timing ───────────────────────────────────────────────────

    def optimize_execution_timing(
        self,
        symbol: str,
        historical_volume: list[dict],
    ) -> dict[str, Any]:
        """Optimize order execution timing."""
        if not historical_volume:
            return {"best_time": "now", "confidence": 0.0}

        # Find times with highest volume
        hourly_volume: dict[int, float] = {}
        for data in historical_volume:
            hour = int(data.get("timestamp", 0) / 3600) % 24
            hourly_volume[hour] = hourly_volume.get(hour, 0) + data.get("volume", 0)

        if hourly_volume:
            best_hour = max(hourly_volume, key=hourly_volume.get)
            return {
                "best_time": f"{best_hour}:00",
                "confidence": min(0.9, hourly_volume[best_hour] / max(1, sum(hourly_volume.values()))),
                "volume_distribution": {str(h): v for h, v in sorted(hourly_volume.items())},
            }

        return {"best_time": "now", "confidence": 0.0}

    # ── Implementation Shortfall ────────────────────────────────────────────

    def calculate_implementation_shortfall(
        self,
        order_id: str,
        decision_price: float,
        fill_price: float,
        quantity: float,
    ) -> float:
        """Calculate implementation shortfall."""
        if decision_price <= 0:
            return 0.0

        # For buys: (fill_price - decision_price) * quantity
        # For sells: (decision_price - fill_price) * quantity
        shortfall = abs(fill_price - decision_price) * quantity
        return shortfall

    def execute_order(
        self,
        order_id: str,
        market_data: MarketData,
    ) -> ExecutionResult:
        """Execute an order."""
        order = self._orders.get(order_id)
        if not order:
            return ExecutionResult(
                order_id=order_id,
                filled_quantity=0,
                avg_price=0.0,
                total_cost=0.0,
                slippage=0.0,
                implementation_shortfall=0.0,
                execution_venue="",
                duration=0.0,
                status="not_found",
            )

        start = time.time()

        # Execute based on order type
        if order.order_type == "market":
            fill_price = market_data.ask if order.side == "buy" else market_data.bid
            filled = min(order.quantity, market_data.bid_size if order.side == "sell" else market_data.ask_size)
        else:
            fill_price = order.limit_price
            filled = order.quantity

        # Calculate costs
        slippage = abs(fill_price - market_data.last_price)
        total_cost = slippage * filled

        # Update order
        order.filled_quantity = filled
        order.avg_fill_price = fill_price
        order.status = "filled" if filled >= order.quantity else "partial"

        result = ExecutionResult(
            order_id=order_id,
            filled_quantity=filled,
            avg_price=fill_price,
            total_cost=total_cost,
            slippage=slippage,
            implementation_shortfall=self.calculate_implementation_shortfall(
                order_id, market_data.last_price, fill_price, filled
            ),
            execution_venue="primary",
            duration=time.time() - start,
            status=order.status,
        )

        self._results[order_id] = result
        return result

    # ── Reporting ──────────────────────────────────────────────────────────

    def get_execution_report(self) -> dict[str, Any]:
        """Generate an execution report."""
        total_fills = sum(r.filled_quantity for r in self._results.values())
        avg_slippage = sum(r.slippage for r in self._results.values()) / max(1, len(self._results))
        avg_shortfall = sum(r.implementation_shortfall for r in self._results.values()) / max(1, len(self._results))

        return {
            "total_orders": len(self._orders),
            "total_executions": len(self._results),
            "total_filled": total_fills,
            "avg_slippage": avg_slippage,
            "avg_implementation_shortfall": avg_shortfall,
            "venues": len(self._venues),
        }
