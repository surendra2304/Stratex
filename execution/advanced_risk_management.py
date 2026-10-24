"""
Stratex/execution/advanced_risk_management.py — Stratex Advanced Risk Management.

Extends Stratex's capabilities with:
1. Value at Risk (VaR) Calculation: calculate portfolio VaR
2. Expected Shortfall: calculate conditional VaR
3. Drawdown Control: monitor and control drawdowns
4. Kelly Criterion: optimal position sizing
5. Risk Decomposition: decompose risk by factor
6. Stress Testing: stress test portfolio against scenarios
7. Correlation Analysis: analyze cross-asset correlations
8. Tail Risk Hedging: identify and hedge tail risks
"""
from __future__ import annotations

import hashlib
import math
import time
from dataclasses import dataclass, field
from typing import Any

from fastapi import APIRouter, HTTPException

router = APIRouter()


@dataclass
class Position:
    """A trading position."""
    symbol: str
    quantity: float
    entry_price: float
    current_price: float = 0.0
    side: str = "long"  # long, short
    stop_loss: float = 0.0
    take_profit: float = 0.0
    risk_pct: float = 0.0  # percentage of portfolio at risk
    max_loss: float = 0.0


@dataclass
class PortfolioRisk:
    """Portfolio risk metrics."""
    total_value: float
    total_risk: float
    sharpe_ratio: float
    sortino_ratio: float
    var_95: float
    var_99: float
    expected_shortfall_95: float
    max_drawdown: float
    current_drawdown: float
    risk_score: float  # 0-100


@dataclass
class StressTestResult:
    """Stress test result."""
    scenario: str
    portfolio_change: float
    portfolio_value_after: float
    var_impact: float
    worst_asset: str
    description: str


@dataclass
class CorrelationAnalysis:
    """Cross-asset correlation analysis."""
    pairs: list[dict]
    matrix: list[list[float]]
    diversification_ratio: float
    concentration_risk: float
    hedge_candidates: list[str]


@dataclass
class TailRiskAssessment:
    """Tail risk assessment."""
    tail_probability: float
    tail_var: float
    hedge_effectiveness: float
    recommended_hedge: str
    hedge_cost: float
    risk_reduction: float


@dataclass
class KellyPosition:
    """Kelly criterion position sizing."""
    symbol: str
    edge: float  # win probability - loss probability
    win_prob: float
    loss_prob: float
    win_ratio: float  # avg_win / avg_loss
    kelly_pct: float  # optimal % of portfolio
    half_kelly_pct: float
    recommended_quantity: float


class AdvancedRiskManager:
    """Advanced risk management engine."""

    def __init__(self):
        self._positions: dict[str, Position] = {}
        self._portfolio_value: float = 100000.0
        self._risk_free_rate: float = 0.04
        self._stress_scenarios = {
            "market_crash_10%": -0.10,
            "market_crash_20%": -0.20,
            "market_crash_30%": -0.30,
            "volatility_spike": -0.15,
            "liquidity_crunch": -0.12,
            "correlation_breakdown": -0.08,
            "sector_rotation": -0.07,
            "geopolitical_event": -0.09,
        }

    # ── Value at Risk (VaR) ──────────────────────────────────────────────

    def calculate_var(
        self,
        positions: list[Position],
        confidence: float = 0.95,
        time_horizon: int = 1,
    ) -> dict:
        """Calculate Value at Risk."""
        # Calculate portfolio returns distribution
        returns = []
        for pos in positions:
            # Historical volatility estimate
            vol = 0.02  # 2% daily volatility assumption
            drift = 0.0001  # slight positive drift

            # Generate returns using normal distribution approximation
            for _ in range(1000):
                # Box-Muller transform for normal distribution
                u1 = max(0.0001, hash(f"{pos.symbol}_{time.time()}_{_}") % 10000 / 10000)
                u2 = max(0.0001, hash(f"{pos.symbol}_{time.time()}_{_}_x") % 10000 / 10000)

                z = math.sqrt(-2 * math.log(u1)) * math.cos(2 * math.pi * u2)
                ret = drift + vol * z

                pnl = pos.quantity * pos.entry_price * ret
                returns.append(pnl)

        returns.sort()

        # Calculate VaR
        idx = int((1 - confidence) * len(returns))
        var = abs(returns[idx])

        # Expected shortfall (conditional VaR)
        tail = returns[:idx + 1]
        es = abs(sum(tail) / len(tail)) if tail else var

        return {
            "var": var,
            "expected_shortfall": es,
            "confidence": confidence,
            "time_horizon": time_horizon,
            "portfolio_value": self._portfolio_value,
            "var_pct": var / self._portfolio_value,
            "es_pct": es / self._portfolio_value,
        }

    # ── Portfolio Risk Metrics ───────────────────────────────────────────

    def calculate_portfolio_risk(
        self,
        positions: list[Position],
        returns: list[float] | None = None,
    ) -> PortfolioRisk:
        """Calculate comprehensive portfolio risk metrics."""
        if not positions:
            return PortfolioRisk(
                total_value=self._portfolio_value,
                total_risk=0.0,
                sharpe_ratio=0.0,
                sortino_ratio=0.0,
                var_95=0.0,
                var_99=0.0,
                expected_shortfall_95=0.0,
                max_drawdown=0.0,
                current_drawdown=0.0,
                risk_score=0.0,
            )

        # Calculate portfolio returns
        if returns is None:
            returns = [0.001, -0.002, 0.003, -0.001, 0.002, -0.003, 0.001, 0.002, -0.002, 0.003]

        port_returns = []
        for ret in returns:
            pnl = sum(
                pos.quantity * pos.entry_price * ret
                for pos in positions
            )
            port_returns.append(pnl)

        # Calculate metrics
        mean_ret = sum(port_returns) / len(port_returns)
        vol = self._stdev(port_returns)
        sharpe = (mean_ret - self._risk_free_rate / 252) / max(0.001, vol)

        # Downside deviation
        downside = [r for r in port_returns if r < 0]
        downside_vol = self._stdev(downside) if downside else 0.001
        sortino = (mean_ret - self._risk_free_rate / 252) / max(0.001, downside_vol)

        # VaR
        var_95 = self.calculate_var(positions, 0.95)["var"]
        var_99 = self.calculate_var(positions, 0.99)["var"]
        es_95 = self.calculate_var(positions, 0.95)["expected_shortfall"]

        # Drawdown
        cumulative = 0.0
        peak = 0.0
        max_dd = 0.0
        for r in port_returns:
            cumulative += r
            peak = max(peak, cumulative)
            dd = (peak - cumulative) / max(1, abs(peak))
            max_dd = max(max_dd, dd)

        current_dd = (peak - cumulative) / max(1, abs(peak))

        # Risk score (0-100)
        risk_score = (
            var_95 / self._portfolio_value * 100 * 10 +
            current_dd * 100 * 5 +
            (1.0 - sharpe) * 30
        )

        return PortfolioRisk(
            total_value=self._portfolio_value,
            total_risk=var_95,
            sharpe_ratio=sharpe,
            sortino_ratio=sortino,
            var_95=var_95,
            var_99=var_99,
            expected_shortfall_95=es_95,
            max_drawdown=max_dd,
            current_drawdown=current_dd,
            risk_score=min(100.0, max(0.0, risk_score)),
        )

    def _stdev(self, values: list[float]) -> float:
        if len(values) < 2:
            return 0.001
        mean = sum(values) / len(values)
        variance = sum((v - mean) ** 2 for v in values) / len(values)
        return math.sqrt(variance)

    # ── Drawdown Control ──────────────────────────────────────────────────

    def check_drawdown_limits(
        self,
        positions: list[Position],
        max_drawdown: float = 0.20,
        warning_drawdown: float = 0.15,
    ) -> dict:
        """Check if portfolio is approaching drawdown limits."""
        risk = self.calculate_portfolio_risk(positions)
        dd = risk.current_drawdown

        if dd >= max_drawdown:
            action = "LIQUIDATE_ALL"
            recommendation = "Critical drawdown exceeded. Immediate liquidation recommended."
        elif dd >= warning_drawdown:
            action = "REDUCE_POSITIONS"
            recommendation = "Warning: Drawdown approaching limit. Reduce positions by 30-50%."
        elif dd >= warning_drawdown * 0.7:
            action = "CAUTION"
            recommendation = "Monitor closely. Consider reducing exposure."
        else:
            action = "NORMAL"
            recommendation = "Drawdown within acceptable limits."

        return {
            "current_drawdown": dd,
            "max_drawdown": max_drawdown,
            "warning_drawdown": warning_drawdown,
            "action": action,
            "recommendation": recommendation,
            "portfolio_value": self._portfolio_value,
        }

    # ── Kelly Criterion ───────────────────────────────────────────────────

    def calculate_kelly_positions(
        self,
        trades: list[dict],
        portfolio_value: float | None = None,
    ) -> list[KellyPosition]:
        """Calculate Kelly criterion position sizing."""
        portfolio_value = portfolio_value or self._portfolio_value

        results = []
        symbols = set(t["symbol"] for t in trades)

        for symbol in symbols:
            symbol_trades = [t for t in trades if t["symbol"] == symbol]
            wins = [t for t in symbol_trades if t["pnl"] > 0]
            losses = [t for t in symbol_trades if t["pnl"] <= 0]

            if not wins or not losses:
                continue

            win_prob = len(wins) / len(symbol_trades)
            loss_prob = 1 - win_prob
            avg_win = sum(t["pnl"] for t in wins) / len(wins)
            avg_loss = abs(sum(t["pnl"] for t in losses) / len(losses))

            if avg_loss == 0:
                continue

            win_ratio = avg_win / avg_loss

            # Kelly fraction: f = (b*p - q) / b
            # where b = win_ratio, p = win_prob, q = loss_prob
            kelly = (win_ratio * win_prob - loss_prob) / win_ratio
            kelly = max(0, kelly)  # Don't go negative

            half_kelly = kelly / 2

            # Recommended quantity
            price = symbol_trades[0].get("price", 100.0)
            recommended_qty = (portfolio_value * half_kelly) / price

            results.append(KellyPosition(
                symbol=symbol,
                edge=win_prob - loss_prob,
                win_prob=win_prob,
                loss_prob=loss_prob,
                win_ratio=win_ratio,
                kelly_pct=kelly,
                half_kelly_pct=half_kelly,
                recommended_quantity=recommended_qty,
            ))

        return sorted(results, key=lambda k: -k.kelly_pct)

    # ── Risk Decomposition ────────────────────────────────────────────────

    def decompose_risk(
        self,
        positions: list[Position],
        factor_returns: dict[str, list[float]] | None = None,
    ) -> dict:
        """Decompose portfolio risk by factor."""
        if not positions:
            return {"total_risk": 0.0, "factors": {}}

        # Default factors
        if factor_returns is None:
            factor_returns = {
                "market": [0.001, -0.002, 0.003, -0.001, 0.002],
                "volatility": [-0.002, 0.001, -0.003, 0.002, -0.001],
                "size": [0.001, 0.002, -0.001, 0.003, -0.002],
                "momentum": [0.002, -0.001, 0.003, 0.001, -0.002],
            }

        factor_risk = {}
        for factor, returns in factor_returns.items():
            exposure = 0.0
            for pos in positions:
                # Assign exposure based on position characteristics
                if pos.side == "long":
                    exposure += pos.quantity * pos.entry_price / self._portfolio_value
                else:
                    exposure -= pos.quantity * pos.entry_price / self._portfolio_value

            factor_var = exposure * self._stdev(returns) * 1.65  # 95% VaR
            factor_risk[factor] = {
                "exposure": exposure,
                "var": factor_var,
                "contribution_pct": abs(factor_var) / max(0.001, sum(abs(v["var"]) for v in factor_risk.values())),
            }

        return {
            "total_risk": sum(abs(v["var"]) for v in factor_risk.values()),
            "factors": factor_risk,
        }

    # ── Stress Testing ───────────────────────────────────────────────────

    def stress_test(
        self,
        positions: list[Position],
        scenarios: dict[str, float] | None = None,
    ) -> list[StressTestResult]:
        """Stress test portfolio against scenarios."""
        scenarios = scenarios or self._stress_scenarios
        results = []

        for scenario, impact in scenarios.items():
            total_change = 0.0
            worst_asset = ""
            worst_change = 0.0

            for pos in positions:
                # Apply scenario impact
                if pos.side == "long":
                    change = pos.quantity * pos.entry_price * impact
                else:
                    change = -pos.quantity * pos.entry_price * impact

                total_change += change

                if change < worst_change:
                    worst_change = change
                    worst_asset = pos.symbol

            results.append(StressTestResult(
                scenario=scenario,
                portfolio_change=total_change,
                portfolio_value_after=self._portfolio_value + total_change,
                var_impact=abs(total_change),
                worst_asset=worst_asset,
                description=f"{scenario.replace('_', ' ').title()}",
            ))

        return sorted(results, key=lambda r: -r.var_impact)

    # ── Correlation Analysis ─────────────────────────────────────────────

    def analyze_correlations(
        self,
        returns_by_symbol: dict[str, list[float]],
    ) -> CorrelationAnalysis:
        """Analyze cross-asset correlations."""
        symbols = list(returns_by_symbol.keys())
        n = len(symbols)

        if n < 2:
            return CorrelationAnalysis(
                pairs=[], matrix=[], diversification_ratio=1.0,
                concentration_risk=0.0, hedge_candidates=[],
            )

        # Calculate correlation matrix
        matrix = []
        for i in range(n):
            row = []
            for j in range(n):
                if i == j:
                    row.append(1.0)
                else:
                    r = self._correlation(
                        returns_by_symbol[symbols[i]],
                        returns_by_symbol[symbols[j]],
                    )
                    row.append(r)
            matrix.append(row)

        # Find pairs with high correlation
        pairs = []
        for i in range(n):
            for j in range(i+1, n):
                pairs.append({
                    "pair": f"{symbols[i]}/{symbols[j]}",
                    "correlation": matrix[i][j],
                })

        # Diversification ratio
        avg_corr = sum(
            matrix[i][j] for i in range(n) for j in range(i+1, n)
        ) / (n * (n - 1) / 2)
        diversification_ratio = (1.0 - avg_corr) / (1.0 - 1.0/n) if n > 1 else 1.0

        # Concentration risk
        concentration = 1.0 - diversification_ratio

        # Find hedge candidates (highly negatively correlated)
        hedge_candidates = []
        for pair in pairs:
            if pair["correlation"] < -0.5:
                hedge_candidates.append(pair["pair"])

        return CorrelationAnalysis(
            pairs=sorted(pairs, key=lambda p: -abs(p["correlation"])),
            matrix=matrix,
            diversification_ratio=diversification_ratio,
            concentration_risk=concentration,
            hedge_candidates=hedge_candidates,
        )

    def _correlation(self, x: list[float], y: list[float]) -> float:
        if len(x) < 2 or len(y) < 2:
            return 0.0
        n = min(len(x), len(y))
        x, y = x[:n], y[:n]
        mean_x = sum(x) / n
        mean_y = sum(y) / n
        cov = sum((x[i] - mean_x) * (y[i] - mean_y) for i in range(n)) / n
        std_x = self._stdev(x)
        std_y = self._stdev(y)
        if std_x == 0 or std_y == 0:
            return 0.0
        return cov / (std_x * std_y)

    # ── Tail Risk Hedging ─────────────────────────────────────────────────

    def assess_tail_risk(
        self,
        positions: list[Position],
        returns: list[float] | None = None,
    ) -> TailRiskAssessment:
        """Assess tail risk and recommend hedges."""
        if returns is None:
            returns = [0.001, -0.002, 0.003, -0.001, 0.002, -0.003, 0.001, 0.002, -0.002, 0.003]

        # Calculate tail statistics
        sorted_returns = sorted(returns)
        tail_5pct_idx = int(0.05 * len(sorted_returns))
        tail_returns = sorted_returns[:tail_5pct_idx + 1]

        tail_prob = len(tail_returns) / len(sorted_returns)
        tail_var = abs(sum(tail_returns) / len(tail_returns)) * self._portfolio_value

        # Recommend hedge
        if tail_var > self._portfolio_value * 0.05:
            recommended_hedge = "put_options"
            hedge_cost = self._portfolio_value * 0.02
            risk_reduction = 0.6
            effectiveness = 0.7
        elif tail_var > self._portfolio_value * 0.03:
            recommended_hedge = "inverse_etf"
            hedge_cost = self._portfolio_value * 0.01
            risk_reduction = 0.5
            effectiveness = 0.6
        else:
            recommended_hedge = "no_hedge_needed"
            hedge_cost = 0.0
            risk_reduction = 0.0
            effectiveness = 0.0

        return TailRiskAssessment(
            tail_probability=tail_prob,
            tail_var=tail_var,
            hedge_effectiveness=effectiveness,
            recommended_hedge=recommended_hedge,
            hedge_cost=hedge_cost,
            risk_reduction=risk_reduction,
        )


# Module-level singleton
risk_manager = AdvancedRiskManager()


# ── API Routes ──────────────────────────────────────────────────────────────


@router.post("/api/v1/risk/var")
async def calculate_var(payload: dict):
    """Calculate Value at Risk."""
    positions = [
        Position(
            symbol=p["symbol"],
            quantity=p["quantity"],
            entry_price=p["entry_price"],
            current_price=p.get("current_price", p["entry_price"]),
            side=p.get("side", "long"),
        )
        for p in payload["positions"]
    ]
    result = risk_manager.calculate_var(
        positions,
        confidence=payload.get("confidence", 0.95),
        time_horizon=payload.get("time_horizon", 1),
    )
    return result


@router.post("/api/v1/risk/portfolio")
async def portfolio_risk(payload: dict):
    """Calculate portfolio risk metrics."""
    positions = [
        Position(
            symbol=p["symbol"],
            quantity=p["quantity"],
            entry_price=p["entry_price"],
            side=p.get("side", "long"),
        )
        for p in payload["positions"]
    ]
    result = risk_manager.calculate_portfolio_risk(
        positions,
        payload.get("returns"),
    )
    return result.__dict__


@router.post("/api/v1/risk/drawdown")
async def check_drawdown(payload: dict):
    """Check drawdown limits."""
    positions = [
        Position(
            symbol=p["symbol"],
            quantity=p["quantity"],
            entry_price=p["entry_price"],
            side=p.get("side", "long"),
        )
        for p in payload["positions"]
    ]
    result = risk_manager.check_drawdown_limits(
        positions,
        payload.get("max_drawdown", 0.20),
        payload.get("warning_drawdown", 0.15),
    )
    return result


@router.post("/api/v1/risk/kelly")
async def kelly_positions(payload: dict):
    """Calculate Kelly positions."""
    result = risk_manager.calculate_kelly_positions(
        payload["trades"],
        payload.get("portfolio_value"),
    )
    return [r.__dict__ for r in result]


@router.post("/api/v1/risk/decompose")
async def decompose_risk(payload: dict):
    """Decompose risk by factor."""
    positions = [
        Position(
            symbol=p["symbol"],
            quantity=p["quantity"],
            entry_price=p["entry_price"],
            side=p.get("side", "long"),
        )
        for p in payload["positions"]
    ]
    result = risk_manager.decompose_risk(
        positions,
        payload.get("factor_returns"),
    )
    return result


@router.post("/api/v1/risk/stress")
async def stress_test(payload: dict):
    """Stress test portfolio."""
    positions = [
        Position(
            symbol=p["symbol"],
            quantity=p["quantity"],
            entry_price=p["entry_price"],
            side=p.get("side", "long"),
        )
        for p in payload["positions"]
    ]
    results = risk_manager.stress_test(
        positions,
        payload.get("scenarios"),
    )
    return [r.__dict__ for r in results]


@router.post("/api/v1/risk/correlation")
async def analyze_correlations(payload: dict):
    """Analyze correlations."""
    result = risk_manager.analyze_correlations(
        payload["returns_by_symbol"],
    )
    return {
        "pairs": result.pairs,
        "diversification_ratio": result.diversification_ratio,
        "concentration_risk": result.concentration_risk,
        "hedge_candidates": result.hedge_candidates,
    }


@router.post("/api/v1/risk/tail")
async def assess_tail_risk(payload: dict):
    """Assess tail risk."""
    positions = [
        Position(
            symbol=p["symbol"],
            quantity=p["quantity"],
            entry_price=p["entry_price"],
            side=p.get("side", "long"),
        )
        for p in payload["positions"]
    ]
    result = risk_manager.assess_tail_risk(
        positions,
        payload.get("returns"),
    )
    return result.__dict__
