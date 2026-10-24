"""
Stratex/execution/flash_crash_detector.py — Stratex Flash Crash Detector.

Flash crash prediction, market stress detection, circuit breaker activation,
and extreme move protection.
"""
from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from fastapi import APIRouter, HTTPException

router = APIRouter()


@dataclass
class MarketStressLevel:
    """Market stress assessment."""
    symbol: str
    stress_score: float = 0.0  # 0 to 100
    stress_level: str = "normal"  # normal, elevated, high, extreme, critical
    volatility: float = 0.0
    volume_spike: float = 0.0
    price_velocity: float = 0.0
    assessed_at: float = field(default_factory=time.time)


@dataclass
class FlashCrashEvent:
    """A detected flash crash event."""
    event_id: str
    symbol: str
    crash_type: str = "flash_crash"  # flash_crash, flash_boom, liquidity_gap, circuit_breaker
    start_price: float = 0.0
    low_price: float = 0.0
    high_price: float = 0.0
    duration_sec: float = 0.0
    max_drawdown: float = 0.0
    circuit_breaker_triggered: bool = False


@dataclass
class ProtectionPlan:
    """A protection plan for extreme market conditions."""
    plan_id: str
    symbol: str
    max_order_size: float = 0.0
    price_limit: float = 0.0
    kill_switch: bool = True
    cooling_period: float = 0.0
    thresholds: dict = field(default_factory=dict)


class FlashCrashDetector:
    """Engine for flash crash detection and protection."""

    def __init__(self) -> None:
        self._stress: list[MarketStressLevel] = []
        self._events: list[FlashCrashEvent] = []
        self._plans: dict[str, ProtectionPlan] = {}

    def assess_stress(self, symbol: str, volatility: float = 0.0,
                      volume_spike: float = 1.0, price_velocity: float = 0.0) -> MarketStressLevel:
        """Assess market stress."""
        stress = MarketStressLevel(symbol=symbol)
        stress.volatility = volatility
        stress.volume_spike = volume_spike
        stress.price_velocity = price_velocity

        stress.stress_score = (
            volatility * 30 + (volume_spike - 1.0) * 20 + price_velocity * 50
        )
        stress.stress_score = max(0.0, min(100.0, stress.stress_score))

        if stress.stress_score > 90:
            stress.stress_level = "critical"
        elif stress.stress_score > 75:
            stress.stress_level = "extreme"
        elif stress.stress_score > 50:
            stress.stress_level = "high"
        elif stress.stress_score > 25:
            stress.stress_level = "elevated"
        else:
            stress.stress_level = "normal"

        self._stress.append(stress)
        return stress

    def detect_flash_crash(self, symbol: str, start_price: float,
                           low_price: float, high_price: float,
                           duration_sec: float = 0.0) -> FlashCrashEvent:
        """Detect a flash crash event."""
        event_id = f"fc_{uuid.uuid4().hex[:12]}"
        event = FlashCrashEvent(
            event_id=event_id, symbol=symbol,
            start_price=start_price, low_price=low_price,
            high_price=high_price, duration_sec=duration_sec,
        )
        event.max_drawdown = (start_price - low_price) / max(0.01, start_price)
        event.circuit_breaker_triggered = event.max_drawdown > 0.10  # 10% drop triggers circuit breaker

        self._events.append(event)
        return event

    def create_protection_plan(self, symbol: str, max_order_size: float = 0.0,
                               price_limit: float = 0.0, kill_switch: bool = True,
                               cooling_period: float = 60.0) -> ProtectionPlan:
        """Create a protection plan for a symbol."""
        plan_id = f"prot_{uuid.uuid4().hex[:12]}"
        plan = ProtectionPlan(
            plan_id=plan_id, symbol=symbol,
            max_order_size=max_order_size, price_limit=price_limit,
            kill_switch=kill_switch, cooling_period=cooling_period,
        )
        plan.thresholds = {
            "stress_level_trigger": "extreme",
            "max_drawdown_pct": 5.0,
            "volume_spike_threshold": 3.0,
            "velocity_threshold": 0.1,
        }
        self._plans[plan_id] = plan
        return plan

    def get_stress_history(self, symbol: str | None = None) -> list[dict[str, Any]]:
        """Get stress history."""
        stress = self._stress
        if symbol:
            stress = [s for s in stress if s.symbol == symbol]
        return [
            {"symbol": s.symbol, "stress_score": s.stress_score,
             "level": s.stress_level, "volatility": s.volatility}
            for s in stress[-20:]
        ]

    def get_events(self, symbol: str | None = None) -> list[dict[str, Any]]:
        """Get flash crash events."""
        events = self._events
        if symbol:
            events = [e for e in events if e.symbol == symbol]
        return [
            {"symbol": e.symbol, "type": e.crash_type,
             "max_drawdown": e.max_drawdown, "circuit_breaker": e.circuit_breaker_triggered}
            for e in events
        ]

    def get_report(self) -> dict[str, Any]:
        """Get engine report."""
        return {
            "total_stress_checks": len(self._stress),
            "total_events": len(self._events),
            "total_plans": len(self._plans),
            "circuit_breakers_triggered": sum(1 for e in self._events if e.circuit_breaker_triggered),
            "engine_status": "operational",
        }


engine = FlashCrashDetector()


@router.post("/trading/flashcrash/stress")
async def assess_stress(symbol: str, volatility: float = 0.0,
                        volume_spike: float = 1.0, price_velocity: float = 0.0):
    """Assess market stress."""
    stress = engine.assess_stress(symbol, volatility, volume_spike, price_velocity)
    return {"symbol": symbol, "stress_score": stress.stress_score, "level": stress.stress_level}


@router.post("/trading/flashcrash/detect")
async def detect_flash_crash(symbol: str, start_price: float, low_price: float,
                             high_price: float, duration_sec: float = 0.0):
    """Detect a flash crash event."""
    event = engine.detect_flash_crash(symbol, start_price, low_price, high_price, duration_sec)
    return {
        "event_id": event.event_id,
        "max_drawdown": event.max_drawdown,
        "circuit_breaker": event.circuit_breaker_triggered,
    }


@router.post("/trading/flashcrash/protection")
async def create_protection_plan(symbol: str, max_order_size: float = 0.0,
                                 price_limit: float = 0.0, kill_switch: bool = True,
                                 cooling_period: float = 60.0):
    """Create a protection plan."""
    plan = engine.create_protection_plan(symbol, max_order_size, price_limit, kill_switch, cooling_period)
    return {"plan_id": plan.plan_id, "symbol": symbol, "kill_switch": plan.kill_switch}


@router.get("/trading/flashcrash/stress")
async def get_stress_history(symbol: str | None = None):
    """Get stress history."""
    return {"stress": engine.get_stress_history(symbol)}


@router.get("/trading/flashcrash/events")
async def get_events(symbol: str | None = None):
    """Get flash crash events."""
    return {"events": engine.get_events(symbol)}


@router.get("/trading/flashcrash/report")
async def flashcrash_report():
    """Get flash crash detector report."""
    return engine.get_report()
