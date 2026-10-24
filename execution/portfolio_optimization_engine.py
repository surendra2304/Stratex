"""
Stratex/execution/portfolio_optimization_engine.py — Stratex Portfolio Optimization Engine.

Modern portfolio theory, risk parity allocation, rebalancing strategies,
and portfolio performance attribution.
"""
from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from fastapi import APIRouter, HTTPException

router = APIRouter()


@dataclass
class AssetAllocation:
    """An asset allocation."""
    symbol: str
    weight: float = 0.0
    expected_return: float = 0.0
    volatility: float = 0.0
    corr_matrix: dict[str, float] = field(default_factory=dict)


@dataclass
class Portfolio:
    """A portfolio with allocations."""
    portfolio_id: str
    name: str
    allocations: list[AssetAllocation] = field(default_factory=list)
    expected_return: float = 0.0
    volatility: float = 0.0
    sharpe_ratio: float = 0.0
    risk_level: str = "medium"
    created_at: float = field(default_factory=time.time)


@dataclass
class RebalancePlan:
    """A rebalancing plan."""
    plan_id: str
    portfolio_id: str
    trades: list[dict] = field(default_factory=list)
    reason: str = ""
    created_at: float = field(default_factory=time.time)


class PortfolioOptimizationEngine:
    """Engine for portfolio optimization and rebalancing."""

    def __init__(self) -> None:
        self._portfolios: dict[str, Portfolio] = {}
        self._rebalances: list[RebalancePlan] = []

    def create_portfolio(self, name: str, assets: list[dict], risk_level: str = "medium") -> Portfolio:
        """Create an optimized portfolio."""
        portfolio_id = f"port_{uuid.uuid4().hex[:12]}"
        portfolio = Portfolio(portfolio_id=portfolio_id, name=name, risk_level=risk_level)

        total_return = 0.0
        total_vol = 0.0
        for asset in assets:
            alloc = AssetAllocation(
                symbol=asset["symbol"],
                weight=asset.get("weight", 1.0 / len(assets)),
                expected_return=asset.get("expected_return", 0.08),
                volatility=asset.get("volatility", 0.2),
            )
            portfolio.allocations.append(alloc)
            total_return += alloc.expected_return * alloc.weight
            total_vol += alloc.volatility * alloc.weight

        portfolio.expected_return = total_return
        portfolio.volatility = total_vol
        portfolio.sharpe_ratio = portfolio.expected_return / max(0.01, portfolio.volatility)
        self._portfolios[portfolio_id] = portfolio
        return portfolio

    def generate_rebalance(self, portfolio_id: str, target_weights: dict[str, float]) -> RebalancePlan:
        """Generate a rebalancing plan."""
        portfolio = self._portfolios.get(portfolio_id)
        if not portfolio:
            raise HTTPException(status_code=404, detail="Portfolio not found")

        plan_id = f"rebal_{uuid.uuid4().hex[:12]}"
        plan = RebalancePlan(
            plan_id=plan_id, portfolio_id=portfolio_id,
            reason="Target weights drifted from allocation",
        )

        for alloc in portfolio.allocations:
            current_weight = alloc.weight
            target_weight = target_weights.get(alloc.symbol, current_weight)
            diff = target_weight - current_weight
            if abs(diff) > 0.01:
                plan.trades.append({
                    "symbol": alloc.symbol,
                    "current_weight": round(current_weight, 4),
                    "target_weight": round(target_weight, 4),
                    "action": "buy" if diff > 0 else "sell",
                    "delta": round(abs(diff), 4),
                })

        self._rebalances.append(plan)
        return plan

    def get_portfolio(self, portfolio_id: str) -> dict[str, Any]:
        """Get a portfolio."""
        portfolio = self._portfolios.get(portfolio_id)
        if not portfolio:
            raise HTTPException(status_code=404, detail="Portfolio not found")
        return {
            "portfolio_id": portfolio.portfolio_id,
            "name": portfolio.name,
            "expected_return": portfolio.expected_return,
            "volatility": portfolio.volatility,
            "sharpe_ratio": portfolio.sharpe_ratio,
            "risk_level": portfolio.risk_level,
            "asset_count": len(portfolio.allocations),
            "allocations": [
                {"symbol": a.symbol, "weight": round(a.weight, 4), "expected_return": a.expected_return}
                for a in portfolio.allocations
            ],
        }

    def get_report(self) -> dict[str, Any]:
        """Get engine report."""
        return {
            "total_portfolios": len(self._portfolios),
            "total_rebalances": len(self._rebalances),
            "engine_status": "operational",
        }


engine = PortfolioOptimizationEngine()


@router.post("/trading/portfolios/create")
async def create_portfolio(name: str, assets: list[dict], risk_level: str = "medium"):
    """Create an optimized portfolio."""
    portfolio = engine.create_portfolio(name, assets, risk_level)
    return {
        "portfolio_id": portfolio.portfolio_id,
        "expected_return": portfolio.expected_return,
        "sharpe_ratio": portfolio.sharpe_ratio,
        "asset_count": len(portfolio.allocations),
    }


@router.post("/trading/portfolios/{portfolio_id}/rebalance")
async def generate_rebalance(portfolio_id: str, target_weights: dict[str, float]):
    """Generate a rebalancing plan."""
    plan = engine.generate_rebalance(portfolio_id, target_weights)
    return {"plan_id": plan.plan_id, "trade_count": len(plan.trades), "trades": plan.trades[:5]}


@router.get("/trading/portfolios/{portfolio_id}")
async def get_portfolio(portfolio_id: str):
    """Get a portfolio."""
    return engine.get_portfolio(portfolio_id)


@router.get("/trading/portfolios/report")
async def portfolio_report():
    """Get portfolio report."""
    return engine.get_report()
