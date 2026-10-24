"""
Stratex/execution/cross_chain_yield_optimizer.py — Stratex Cross-Chain DeFi Yield Optimizer.

Optimizes yield across multiple blockchain networks: liquidity pool analysis,
bridge optimization, gas cost minimization, risk-adjusted returns, and
cross-chain arbitrage detection.
"""
from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass, field
from typing import Any

from fastapi import APIRouter, HTTPException

router = APIRouter()


@dataclass
class ChainConfig:
    """Blockchain chain configuration."""
    chain_id: str
    name: str
    native_token: str
    gas_price_gwei: float = 1.0
    block_time_sec: float = 12.0
    tvl_usd: float = 0.0
    defi_protocols: int = 0
    bridge_count: int = 0


@dataclass
class YieldOpportunity:
    """A yield opportunity on a chain."""
    opportunity_id: str
    chain_id: str
    protocol: str
    pool_name: str
    token_pair: str
    apy: float = 0.0
    tvl_usd: float = 0.0
    risk_score: float = 0.5  # 0-1
    fee_tier: float = 0.003
    liquidity_depth: float = 0.0
    volume_24h: float = 0.0
    lock_period_days: int = 0
    incentives: list[str] = field(default_factory=list)


@dataclass
class BridgeConfig:
    """A cross-chain bridge configuration."""
    bridge_id: str
    name: str
    source_chain: str
    target_chain: str
    supported_tokens: list[str] = field(default_factory=list)
    fee_pct: float = 0.0
    estimated_time_min: float = 0.0
    max_transfer_usd: float = 0.0
    security_score: float = 0.5
    tvl_usd: float = 0.0


@dataclass
class ArbitrageOpportunity:
    """A cross-chain arbitrage opportunity."""
    arb_id: str
    token: str
    buy_chain: str
    buy_protocol: str
    buy_price: float
    sell_chain: str
    sell_protocol: str
    sell_price: float
    price_diff_pct: float
    estimated_profit_usd: float
    estimated_gas_usd: float
    net_profit_usd: float
    risk_level: str = "medium"


@dataclass
class OptimizationStrategy:
    """A yield optimization strategy."""
    strategy_id: str
    name: str
    description: str
    allocations: dict = field(default_factory=dict)  # chain → {protocol: {amount, apy}}
    expected_apy: float = 0.0
    expected_risk: float = 0.0
    estimated_gas_cost: float = 0.0
    net_return_usd: float = 0.0
    roi: float = 0.0
    diversification_score: float = 0.0


@dataclass
class RiskAssessment:
    """Risk assessment for a strategy."""
    strategy_id: str
    overall_risk: float
    contract_risk: float
    liqudity_risk: float
    bridging_risk: float
    market_risk: float
    oracle_risk: float
    recommendations: list[str] = field(default_factory=list)


class CrossChainYieldOptimizer:
    """Cross-chain DeFi yield optimization engine."""

    def __init__(self):
        self._chains: dict[str, ChainConfig] = {}
        self._opportunities: dict[str, YieldOpportunity] = {}
        self._bridges: dict[str, BridgeConfig] = {}
        self._arbitrages: dict[str, ArbitrageOpportunity] = {}
        self._strategies: dict[str, OptimizationStrategy] = {}

        # Register default chains
        for chain_id, name, token in [
            ("ethereum", "Ethereum", "ETH"),
            ("bsc", "BSC", "BNB"),
            ("polygon", "Polygon", "MATIC"),
            ("arbitrum", "Arbitrum", "ETH"),
            ("optimism", "Optimism", "ETH"),
            ("avalanche", "Avalanche", "AVAX"),
        ]:
            self._chains[chain_id] = ChainConfig(
                chain_id=chain_id,
                name=name,
                native_token=token,
                gas_price_gwei=1.5 if chain_id == "ethereum" else 0.5,
                tvl_usd=50e9 if chain_id == "ethereum" else 10e9,
                defi_protocols=200,
                bridge_count=5,
            )

    def register_chain(
        self,
        chain_id: str,
        name: str,
        native_token: str,
        gas_price_gwei: float = 1.0,
    ) -> ChainConfig:
        """Register a blockchain chain."""
        chain = ChainConfig(
            chain_id=chain_id,
            name=name,
            native_token=native_token,
            gas_price_gwei=gas_price_gwei,
            tvl_usd=10e9,
            defi_protocols=100,
            bridge_count=3,
        )
        self._chains[chain_id] = chain
        return chain

    def add_yield_opportunity(
        self,
        chain_id: str,
        protocol: str,
        pool_name: str,
        token_pair: str,
        apy: float,
        tvl_usd: float,
        risk_score: float = 0.5,
    ) -> YieldOpportunity:
        """Add a yield opportunity."""
        opp = YieldOpportunity(
            opportunity_id=hashlib.md5(
                f"opp:{chain_id}:{protocol}:{pool_name}:{time.time()}".encode()
            ).hexdigest()[:12],
            chain_id=chain_id,
            protocol=protocol,
            pool_name=pool_name,
            token_pair=token_pair,
            apy=apy,
            tvl_usd=tvl_usd,
            risk_score=risk_score,
        )
        self._opportunities[opp.opportunity_id] = opp
        return opp

    def add_bridge(
        self,
        name: str,
        source_chain: str,
        target_chain: str,
        fee_pct: float = 0.0,
        security_score: float = 0.5,
    ) -> BridgeConfig:
        """Add a cross-chain bridge."""
        bridge = BridgeConfig(
            bridge_id=hashlib.md5(
                f"bridge:{name}:{source_chain}:{target_chain}:{time.time()}".encode()
            ).hexdigest()[:12],
            name=name,
            source_chain=source_chain,
            target_chain=target_chain,
            supported_tokens=["USDT", "USDC", "ETH", "BTC"],
            fee_pct=fee_pct,
            estimated_time_min=15.0,
            max_transfer_usd=1e6,
            security_score=security_score,
            tvl_usd=1e9,
        )
        self._bridges[bridge.bridge_id] = bridge
        return bridge

    def detect_arbitrage(self, token: str = "USDC") -> list[ArbitrageOpportunity]:
        """Detect cross-chain arbitrage opportunities."""
        arbitrages = []

        # For each pair of chains
        chains = list(self._chains.keys())
        for i, buy_chain in enumerate(chains):
            for sell_chain in chains[i+1:]:
                # Find opportunities on both chains
                buy_opps = [o for o in self._opportunities.values() if o.chain_id == buy_chain and token in o.token_pair]
                sell_opps = [o for o in self._opportunities.values() if o.chain_id == sell_chain and token in o.token_pair]

                for buy_opp in buy_opps:
                    for sell_opp in sell_opps:
                        # Simulate price difference
                        price_diff = abs(buy_opp.apy - sell_opp.apy) / 100

                        if price_diff > 0.5:  # Only if meaningful
                            amount = 10000.0
                            profit = amount * price_diff / 100
                            gas_cost = (
                                self._chains[buy_chain].gas_price_gwei * 0.001 +
                                self._chains[sell_chain].gas_price_gwei * 0.001
                            )

                            arb = ArbitrageOpportunity(
                                arb_id=hashlib.md5(
                                    f"arb:{token}:{buy_chain}:{sell_chain}:{time.time()}".encode()
                                ).hexdigest()[:12],
                                token=token,
                                buy_chain=buy_chain,
                                buy_protocol=buy_opp.protocol,
                                buy_price=1.0 + price_diff,
                                sell_chain=sell_chain,
                                sell_protocol=sell_opp.protocol,
                                sell_price=1.0,
                                price_diff_pct=price_diff * 100,
                                estimated_profit_usd=profit,
                                estimated_gas_usd=gas_cost,
                                net_profit_usd=profit - gas_cost,
                                risk_level="high" if profit < gas_cost * 2 else "medium",
                            )
                            arbitrages.append(arb)
                            self._arbitrages[arb.arb_id] = arb

        return arbitrages

    def optimize_allocation(
        self,
        capital_usd: float,
        min_apy: float = 5.0,
        max_risk: float = 0.8,
    ) -> OptimizationStrategy:
        """Optimize yield allocation across chains."""
        # Filter opportunities
        valid_opps = [
            o for o in self._opportunities.values()
            if o.apy >= min_apy and o.risk_score <= max_risk
        ]

        # Sort by risk-adjusted APY (APY / risk)
        risk_adjusted = sorted(valid_opps, key=lambda o: o.apy / max(0.1, o.risk_score), reverse=True)

        # Select top opportunities
        selected = risk_adjusted[:5]

        allocations = {}
        total_apy = 0.0
        total_risk = 0.0
        total_gas = 0.0
        total_allocated = 0.0

        for opp in selected:
            # Allocate based on TVL and risk
            allocation_pct = min(0.3, 1.0 / len(selected))
            allocation = capital_usd * allocation_pct

            if allocation > opp.tvl_usd * 0.1:  # Don't exceed 10% of TVL
                allocation = opp.tvl_usd * 0.1

            chain_alloc = allocations.setdefault(opp.chain_id, {})
            chain_alloc[opp.protocol] = {
                "amount": allocation,
                "apy": opp.apy,
                "risk": opp.risk_score,
                "pool": opp.pool_name,
            }

            total_apy += opp.apy * (allocation / capital_usd)
            total_risk += opp.risk_score * (allocation / capital_usd)
            total_gas += self._chains[opp.chain_id].gas_price_gwei * 0.01
            total_allocated += allocation

        # Calculate returns
        expected_return = capital_usd * total_apy / 100
        net_return = expected_return - total_gas
        roi = net_return / max(1, capital_usd) * 100

        # Diversification score
        chains_used = len(allocations)
        diversification = min(1.0, chains_used / 5)

        strategy = OptimizationStrategy(
            strategy_id=hashlib.md5(
                f"strategy:{capital_usd}:{min_apy}:{time.time()}".encode()
            ).hexdigest()[:12],
            name="Cross-Chain Yield Strategy",
            description=f"Optimized allocation across {chains_used} chains",
            allocations=allocations,
            expected_apy=total_apy,
            expected_risk=total_risk,
            estimated_gas_cost=total_gas,
            net_return_usd=net_return,
            roi=roi,
            diversification_score=diversification,
        )

        self._strategies[strategy.strategy_id] = strategy
        return strategy

    def assess_risk(self, strategy_id: str) -> RiskAssessment:
        """Assess risk for a strategy."""
        strategy = self._strategies.get(strategy_id)
        if not strategy:
            raise HTTPException(404, f"Strategy {strategy_id} not found")

        # Calculate risk components
        contract_risk = 0.3
        liqudity_risk = 0.2
        bridging_risk = 0.4
        market_risk = 0.3
        oracle_risk = 0.2

        # Adjust based on allocations
        for chain_alloc in strategy.allocations.values():
            for alloc in chain_alloc.values():
                contract_risk = max(contract_risk, alloc.get("risk", 0.5))
                liqudity_risk = min(1.0, liqudity_risk + 0.1 if alloc.get("amount", 0) > 10000 else 0.05)

        overall_risk = (
            contract_risk * 0.3 +
            liqudity_risk * 0.2 +
            bridging_risk * 0.25 +
            market_risk * 0.15 +
            oracle_risk * 0.1
        )

        recommendations = []
        if overall_risk > 0.6:
            recommendations.append("Reduce allocation to high-risk protocols")
            recommendations.append("Use more secure bridges")
        if bridging_risk > 0.4:
            recommendations.append("Diversify across more bridges")
        if contract_risk > 0.5:
            recommendations.append("Prefer audited protocols")
        if liqudity_risk > 0.3:
            recommendations.append("Use pools with deeper liquidity")

        return RiskAssessment(
            strategy_id=strategy_id,
            overall_risk=overall_risk,
            contract_risk=contract_risk,
            liqudity_risk=liqudity_risk,
            bridging_risk=bridging_risk,
            market_risk=market_risk,
            oracle_risk=oracle_risk,
            recommendations=recommendations,
        )

    def get_report(self) -> dict:
        """Get yield optimizer report."""
        return {
            "total_chains": len(self._chains),
            "total_opportunities": len(self._opportunities),
            "total_bridges": len(self._bridges),
            "total_arbitrages": len(self._arbitrages),
            "total_strategies": len(self._strategies),
            "chains": list(self._chains.keys()),
            "total_tvl_usd": sum(c.tvl_usd for c in self._chains.values()),
            "avg_apy": sum(o.apy for o in self._opportunities.values()) / max(1, len(self._opportunities)),
            "strategies": [
                {
                    "name": s.name,
                    "expected_apy": s.expected_apy,
                    "roi": s.roi,
                    "diversification": s.diversification_score,
                }
                for s in self._strategies.values()
            ],
        }


# Module-level singleton
yield_optimizer = CrossChainYieldOptimizer()


# ── API Routes ──────────────────────────────────────────────────────────────


@router.post("/api/v1/trading/yield/chains")
async def register_chain(payload: dict):
    """Register a blockchain chain."""
    chain = yield_optimizer.register_chain(
        chain_id=payload["chain_id"],
        name=payload["name"],
        native_token=payload["native_token"],
        gas_price_gwei=payload.get("gas_price_gwei", 1.0),
    )
    return {"chain_id": chain.chain_id, "name": chain.name, "status": "registered"}


@router.post("/api/v1/trading/yield/opportunities")
async def add_yield_opportunity(payload: dict):
    """Add a yield opportunity."""
    opp = yield_optimizer.add_yield_opportunity(
        chain_id=payload["chain_id"],
        protocol=payload["protocol"],
        pool_name=payload["pool_name"],
        token_pair=payload["token_pair"],
        apy=payload["apy"],
        tvl_usd=payload["tvl_usd"],
        risk_score=payload.get("risk_score", 0.5),
    )
    return {"opportunity_id": opp.opportunity_id, "protocol": opp.protocol, "apy": opp.apy}


@router.post("/api/v1/trading/yield/bridges")
async def add_bridge(payload: dict):
    """Add a cross-chain bridge."""
    bridge = yield_optimizer.add_bridge(
        name=payload["name"],
        source_chain=payload["source_chain"],
        target_chain=payload["target_chain"],
        fee_pct=payload.get("fee_pct", 0.0),
        security_score=payload.get("security_score", 0.5),
    )
    return {"bridge_id": bridge.bridge_id, "name": bridge.name, "status": "added"}


@router.get("/api/v1/trading/yield/arbitrage")
async def detect_arbitrage(token: str = "USDC"):
    """Detect cross-chain arbitrage opportunities."""
    arbitrages = yield_optimizer.detect_arbitrage(token)
    return {
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
    }


@router.post("/api/v1/trading/yield/optimize")
async def optimize_allocation(payload: dict):
    """Optimize yield allocation."""
    strategy = yield_optimizer.optimize_allocation(
        capital_usd=payload["capital_usd"],
        min_apy=payload.get("min_apy", 5.0),
        max_risk=payload.get("max_risk", 0.8),
    )
    return {
        "strategy_id": strategy.strategy_id,
        "expected_apy": strategy.expected_apy,
        "expected_risk": strategy.expected_risk,
        "net_return_usd": strategy.net_return_usd,
        "roi": strategy.roi,
        "diversification_score": strategy.diversification_score,
        "allocations": strategy.allocations,
    }


@router.get("/api/v1/trading/yield/strategies/{strategy_id}/risk")
async def assess_risk(strategy_id: str):
    """Assess risk for a strategy."""
    return yield_optimizer.assess_risk(strategy_id).__dict__


@router.get("/api/v1/trading/yield/report")
async def get_report():
    """Get yield optimizer report."""
    return yield_optimizer.get_report()
