"""
Stratex/execution/execution_venue_optimizer.py — Stratex Execution Venue Optimizer.

Smart order routing, venue cost comparison, liquidity aggregation,
and execution quality measurement.
"""
from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from fastapi import APIRouter, HTTPException

router = APIRouter()


@dataclass
class VenueStats:
    """Statistics for a trading venue."""
    venue_id: str
    name: str
    fee_bps: float = 0.0
    latency_ms: float = 0.0
    liquidity_score: float = 0.5
    max_order_size: float = 0.0
    available: bool = True
    uptime_pct: float = 99.0


@dataclass
class ExecutionPlan:
    """An execution plan across venues."""
    plan_id: str
    symbol: str
    total_order_size: float = 0.0
    venue_allocations: list[dict] = field(default_factory=list)
    estimated_cost: float = 0.0
    estimated_slippage: float = 0.0
    created_at: float = field(default_factory=time.time)


@dataclass
class ExecutionQuality:
    """Execution quality metrics."""
    quality_id: str
    order_id: str
    symbol: str
    bm_diff: float = 0.0  # benchmark difference
    slippage: float = 0.0
    fill_rate: float = 0.0
    time_to_fill: float = 0.0
    venue_used: str = ""


class ExecutionVenueOptimizer:
    """Engine for execution venue optimization."""

    def __init__(self) -> None:
        self._venues: dict[str, VenueStats] = {}
        self._plans: list[ExecutionPlan] = []
        self._quality: list[ExecutionQuality] = []

    def register_venue(self, name: str, fee_bps: float = 5.0, latency_ms: float = 50.0,
                       liquidity_score: float = 0.5, max_order_size: float = 0.0,
                       available: bool = True, uptime_pct: float = 99.0) -> VenueStats:
        """Register a trading venue."""
        venue_id = f"ven_{uuid.uuid4().hex[:8]}"
        venue = VenueStats(
            venue_id=venue_id, name=name, fee_bps=fee_bps,
            latency_ms=latency_ms, liquidity_score=liquidity_score,
            max_order_size=max_order_size, available=available,
            uptime_pct=uptime_pct,
        )
        self._venues[venue_id] = venue
        return venue

    def optimize_execution(self, symbol: str, order_size: float) -> ExecutionPlan:
        """Optimize execution across venues."""
        plan_id = f"plan_{uuid.uuid4().hex[:12]}"
        plan = ExecutionPlan(plan_id=plan_id, symbol=symbol, total_order_size=order_size)

        # Score venues
        scored = []
        for venue in self._venues.values():
            if not venue.available:
                continue
            cost_score = 1.0 - (venue.fee_bps / 100.0)
            speed_score = 1.0 - (venue.latency_ms / 1000.0)
            liquidity = venue.liquidity_score
            total_score = (cost_score * 0.4 + speed_score * 0.3 + liquidity * 0.3)
            scored.append((venue, total_score))

        scored.sort(key=lambda x: x[1], reverse=True)

        # Allocate across top venues
        remaining = order_size
        for venue, score in scored[:3]:
            allocation = order_size * (score / sum(s for _, s in scored[:3]))
            plan.venue_allocations.append({
                "venue_id": venue.venue_id,
                "venue_name": venue.name,
                "allocation": round(allocation, 2),
                "fee_bps": venue.fee_bps,
            })
            remaining -= allocation
            plan.estimated_cost += allocation * venue.fee_bps / 10000
            plan.estimated_slippage += allocation * (1.0 - venue.liquidity_score) * 0.01

        self._plans.append(plan)
        return plan

    def record_quality(self, order_id: str, symbol: str, bm_diff: float = 0.0,
                       slippage: float = 0.0, fill_rate: float = 0.0,
                       time_to_fill: float = 0.0, venue_used: str = "") -> ExecutionQuality:
        """Record execution quality."""
        quality_id = f"eq_{uuid.uuid4().hex[:12]}"
        quality = ExecutionQuality(
            quality_id=quality_id, order_id=order_id, symbol=symbol,
            bm_diff=bm_diff, slippage=slippage, fill_rate=fill_rate,
            time_to_fill=time_to_fill, venue_used=venue_used,
        )
        self._quality.append(quality)
        return quality

    def get_venues(self) -> list[dict[str, Any]]:
        """Get registered venues."""
        return [
            {"name": v.name, "fee_bps": v.fee_bps, "latency_ms": v.latency_ms,
             "liquidity_score": v.liquidity_score, "available": v.available}
            for v in self._venues.values()
        ]

    def get_report(self) -> dict[str, Any]:
        """Get engine report."""
        return {
            "total_venues": len(self._venues),
            "total_plans": len(self._plans),
            "total_quality_records": len(self._quality),
            "engine_status": "operational",
        }


engine = ExecutionVenueOptimizer()


@router.post("/trading/venues/register")
async def register_venue(name: str, fee_bps: float = 5.0, latency_ms: float = 50.0,
                         liquidity_score: float = 0.5, max_order_size: float = 0.0,
                         available: bool = True, uptime_pct: float = 99.0):
    """Register a trading venue."""
    venue = engine.register_venue(name, fee_bps, latency_ms, liquidity_score,
                                  max_order_size, available, uptime_pct)
    return {"venue_id": venue.venue_id, "name": venue.name, "fee_bps": venue.fee_bps}


@router.post("/trading/venues/optimize")
async def optimize_execution(symbol: str, order_size: float):
    """Optimize execution across venues."""
    plan = engine.optimize_execution(symbol, order_size)
    return {
        "plan_id": plan.plan_id,
        "venue_count": len(plan.venue_allocations),
        "estimated_cost": plan.estimated_cost,
        "estimated_slippage": plan.estimated_slippage,
    }


@router.post("/trading/venues/quality")
async def record_quality(order_id: str, symbol: str, bm_diff: float = 0.0,
                         slippage: float = 0.0, fill_rate: float = 0.0,
                         time_to_fill: float = 0.0, venue_used: str = ""):
    """Record execution quality."""
    quality = engine.record_quality(order_id, symbol, bm_diff, slippage, fill_rate, time_to_fill, venue_used)
    return {"quality_id": quality.quality_id, "fill_rate": quality.fill_rate}


@router.get("/trading/venues")
async def get_venues():
    """Get registered venues."""
    return {"venues": engine.get_venues()}


@router.get("/trading/venues/report")
async def venue_report():
    """Get venue optimizer report."""
    return engine.get_report()
