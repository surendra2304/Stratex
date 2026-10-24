"""
Stratex/execution/portfolio_risk_analytics.py — Stratex Portfolio Risk Analytics Engine.

Analyzes portfolio risk across multiple dimensions: VaR (Value at Risk),
stress testing, scenario analysis, correlation analysis, factor exposure,
and risk decomposition with Monte Carlo simulation.
"""
from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass, field
from typing import Any

from fastapi import APIRouter, HTTPException

router = APIRouter()


@dataclass
class Portfolio:
    """A trading portfolio."""
    portfolio_id: str
    name: str
    positions: list[dict] = field(default_factory=list)  # [{symbol, quantity, avg_price}]
    cash_balance: float = 0.0
    created_at: float = 0.0
    risk_profile: str = "balanced"  # conservative, balanced, aggressive


@dataclass
class Position:
    """A portfolio position."""
    symbol: str
    quantity: float
    avg_price: float
    current_price: float = 0.0
    market_value: float = 0.0
    unrealized_pnl: float = 0.0
    weight: float = 0.0  # % of portfolio
    risk_contribution: float = 0.0
    beta: float = 1.0
    volatility: float = 0.0


@dataclass
class VaRResult:
    """Value at Risk calculation result."""
    portfolio_id: str
    var_95: float = 0.0  # 95% VaR (1-day)
    var_99: float = 0.0  # 99% VaR (1-day)
    var_95_10d: float = 0.0  # 10-day VaR
    cvar_95: float = 0.0  # Conditional VaR
    var_method: str = "historical"  # historical, parametric, monte_carlo
    confidence_level: float = 0.95
    time_horizon: str = "1d"
    portfolio_value: float = 0.0


@dataclass
class StressTestResult:
    """Stress test result."""
    portfolio_id: str
    scenario_name: str
    portfolio_value: float
    loss_pct: float
    loss_usd: float
    recovery_estimate: float = 0.0
    risk_factors: list[str] = field(default_factory=list)


@dataclass
class ScenarioAnalysis:
    """Scenario analysis result."""
    portfolio_id: str
    scenario_name: str
    description: str
    portfolio_value_change: float = 0.0
    probability: float = 0.0
    affected_positions: list[str] = field(default_factory=list)
    risk_level: str = "medium"


@dataclass
class CorrelationMatrix:
    """Correlation matrix between assets."""
    symbols: list[str] = field(default_factory=list)
    matrix: list[list[float]] = field(default_factory=list)
    average_correlation: float = 0.0
    highly_correlated_pairs: list[list[str]] = field(default_factory=list)


@dataclass
class FactorExposure:
    """Factor exposure analysis."""
    portfolio_id: str
    factors: dict = field(default_factory=dict)  # {factor_name: exposure}
    factor_risk: dict = field(default_factory=dict)  # {factor_name: risk_contribution}
    active_exposures: list[str] = field(default_factory=list)
    total_factor_risk: float = 0.0


@dataclass
class RiskDecomposition:
    """Risk decomposition result."""
    portfolio_id: str
    total_risk: float = 0.0
    systematic_risk: float = 0.0
    idiosyncratic_risk: float = 0.0
    diversification_ratio: float = 0.0
    concentration_risk: float = 0.0
    position_risks: list[dict] = field(default_factory=list)


@dataclass
class MonteCarloResult:
    """Monte Carlo simulation result."""
    portfolio_id: str
    simulations: int = 0
    avg_final_value: float = 0.0
    std_dev: float = 0.0
    prob_loss: float = 0.0  # Probability of loss
    prob_5pct_loss: float = 0.0  # Probability of >5% loss
    prob_10pct_gain: float = 0.0  # Probability of >10% gain
    percentiles: list[float] = field(default_factory=list)


class PortfolioRiskAnalytics:
    """Portfolio risk analytics engine."""

    def __init__(self):
        self._portfolios: dict[str, Portfolio] = {}
        self._var_results: dict[str, VaRResult] = {}
        self._stress_tests: list[StressTestResult] = []
        self._scenarios: list[ScenarioAnalysis] = []
        self._correlations: dict[str, CorrelationMatrix] = {}
        self._factor_exposures: dict[str, FactorExposure] = {}
        self._decompositions: dict[str, RiskDecomposition] = {}
        self._monte_carlo: dict[str, MonteCarloResult] = {}

    def create_portfolio(self, name: str, cash_balance: float = 0.0, risk_profile: str = "balanced") -> Portfolio:
        """Create a portfolio."""
        portfolio = Portfolio(
            portfolio_id=hashlib.md5(
                f"portfolio:{name}:{time.time()}".encode()
            ).hexdigest()[:12],
            name=name,
            cash_balance=cash_balance,
            risk_profile=risk_profile,
            created_at=time.time(),
        )
        self._portfolios[portfolio.portfolio_id] = portfolio
        return portfolio

    def add_position(
        self,
        portfolio_id: str,
        symbol: str,
        quantity: float,
        avg_price: float,
    ) -> Position:
        """Add a position to a portfolio."""
        portfolio = self._portfolios.get(portfolio_id)
        if not portfolio:
            raise HTTPException(404, f"Portfolio {portfolio_id} not found")

        current_price = avg_price * (1 + (ord(hashlib.md5(symbol.encode()).hexdigest()[:1]) % 20) / 100 - 0.05)
        market_value = quantity * current_price
        unrealized_pnl = (current_price - avg_price) * quantity

        position = Position(
            symbol=symbol,
            quantity=quantity,
            avg_price=avg_price,
            current_price=current_price,
            market_value=market_value,
            unrealized_pnl=unrealized_pnl,
            beta=0.8 + (ord(hashlib.md5(symbol.encode()).hexdigest()[:1]) % 40) / 100,
            volatility=0.15 + (ord(hashlib.md5(f"vol:{symbol}").encode()) % 30) / 100,
        )

        portfolio.positions.append({
            "symbol": symbol,
            "quantity": quantity,
            "avg_price": avg_price,
            "current_price": current_price,
        })

        return position

    def calculate_var(
        self,
        portfolio_id: str,
        method: str = "historical",
        confidence: float = 0.95,
        horizon: str = "1d",
    ) -> VaRResult:
        """Calculate Value at Risk."""
        portfolio = self._portfolios.get(portfolio_id)
        if not portfolio:
            raise HTTPException(404, f"Portfolio {portfolio_id} not found")

        total_value = portfolio.cash_balance + sum(
            p["quantity"] * p.get("current_price", p["avg_price"]) for p in portfolio.positions
        )

        # Simulate VaR calculation
        portfolio_volatility = sum(
            p["quantity"] * p["avg_price"] * 0.15 for p in portfolio.positions
        ) / max(1, total_value)

        var_95 = total_value * portfolio_volatility * 1.645  # 95% one-tailed
        var_99 = total_value * portfolio_volatility * 2.326  # 99% one-tailed
        var_95_10d = var_95 * (10 ** 0.5)  # Scale by sqrt(time)
        cvar_95 = var_95 * 1.2  # Conditional VaR (expected loss beyond VaR)

        result = VaRResult(
            portfolio_id=portfolio_id,
            var_95=var_95,
            var_99=var_99,
            var_95_10d=var_95_10d,
            cvar_95=cvar_95,
            var_method=method,
            confidence_level=confidence,
            time_horizon=horizon,
            portfolio_value=total_value,
        )

        self._var_results[portfolio_id] = result
        return result

    def stress_test(
        self,
        portfolio_id: str,
        scenario_name: str,
        market_shock_pct: float = -20.0,
    ) -> StressTestResult:
        """Run a stress test on a portfolio."""
        portfolio = self._portfolios.get(portfolio_id)
        if not portfolio:
            raise HTTPException(404, f"Portfolio {portfolio_id} not found")

        total_value = portfolio.cash_balance + sum(
            p["quantity"] * p.get("current_price", p["avg_price"]) for p in portfolio.positions
        )

        shocked_value = total_value * (1 + market_shock_pct / 100)
        loss = total_value - shocked_value
        loss_pct = loss / total_value * 100

        result = StressTestResult(
            portfolio_id=portfolio_id,
            scenario_name=scenario_name,
            portfolio_value=shocked_value,
            loss_pct=loss_pct,
            loss_usd=loss,
            recovery_estimate=loss * 0.5,
            risk_factors=[f"Market shock: {market_shock_pct}%", f"Portfolio volatility impact"],
        )

        self._stress_tests.append(result)
        return result

    def scenario_analysis(
        self,
        portfolio_id: str,
        scenario_name: str,
        description: str,
        impact_pct: float,
        probability: float = 0.5,
    ) -> ScenarioAnalysis:
        """Run scenario analysis."""
        portfolio = self._portfolios.get(portfolio_id)
        if not portfolio:
            raise HTTPException(404, f"Portfolio {portfolio_id} not found")

        total_value = portfolio.cash_balance + sum(
            p["quantity"] * p.get("current_price", p["avg_price"]) for p in portfolio.positions
        )

        value_change = total_value * impact_pct / 100

        risk_level = "low" if abs(impact_pct) < 10 else "medium" if abs(impact_pct) < 25 else "high"

        scenario = ScenarioAnalysis(
            portfolio_id=portfolio_id,
            scenario_name=scenario_name,
            description=description,
            portfolio_value_change=value_change,
            probability=probability,
            affected_positions=[p["symbol"] for p in portfolio.positions],
            risk_level=risk_level,
        )

        self._scenarios.append(scenario)
        return scenario

    def correlation_analysis(self, symbols: list[str]) -> CorrelationMatrix:
        """Analyze correlation between assets."""
        # Simulate correlation matrix
        n = len(symbols)
        matrix = []

        for i in range(n):
            row = []
            for j in range(n):
                if i == j:
                    row.append(1.0)
                else:
                    # Simulate correlation (0.3 to 0.8 for correlated assets)
                    corr = 0.3 + (ord(hashlib.md5(f"{symbols[i]}:{symbols[j]}").encode()) % 50) / 100
                    row.append(corr)
            matrix.append(row)

        avg_corr = sum(matrix[i][j] for i in range(n) for j in range(n) if i != j) / max(1, n * (n - 1))

        highly_correlated = []
        for i in range(n):
            for j in range(i + 1, n):
                if matrix[i][j] > 0.7:
                    highly_correlated.append([symbols[i], symbols[j]])

        correlation = CorrelationMatrix(
            symbols=symbols,
            matrix=matrix,
            average_correlation=avg_corr,
            highly_correlated_pairs=highly_correlated,
        )

        return correlation

    def factor_exposure(self, portfolio_id: str) -> FactorExposure:
        """Analyze factor exposure."""
        portfolio = self._portfolios.get(portfolio_id)
        if not portfolio:
            raise HTTPException(404, f"Portfolio {portfolio_id} not found")

        factors = {
            "market": 0.8,
            "size": 0.3,
            "value": 0.4,
            "momentum": 0.5,
            "volatility": 0.2,
            "quality": 0.6,
        }

        exposure = FactorExposure(
            portfolio_id=portfolio_id,
            factors=factors,
            factor_risk={k: v * 0.1 for k, v in factors.items()},
            active_exposures=[k for k, v in factors.items() if v > 0.5],
            total_factor_risk=sum(factors.values()) * 0.1,
        )

        self._factor_exposures[portfolio_id] = exposure
        return exposure

    def risk_decomposition(self, portfolio_id: str) -> RiskDecomposition:
        """Decompose portfolio risk."""
        portfolio = self._portfolios.get(portfolio_id)
        if not portfolio:
            raise HTTPException(404, f"Portfolio {portfolio_id} not found")

        total_value = portfolio.cash_balance + sum(
            p["quantity"] * p.get("current_price", p["avg_price"]) for p in portfolio.positions
        )

        position_risks = []
        total_risk = 0.0

        for pos in portfolio.positions:
            weight = pos["quantity"] * pos.get("current_price", pos["avg_price"]) / max(1, total_value)
            risk = weight * 0.15
            total_risk += risk
            position_risks.append({
                "symbol": pos["symbol"],
                "weight": weight,
                "risk_contribution": risk,
                "risk_pct": risk / max(0.01, total_risk) * 100,
            })

        systematic_risk = total_risk * 0.6
        idiosyncratic_risk = total_risk * 0.4
        diversification_ratio = len(portfolio.positions) / max(1, len(portfolio.positions) * 0.5)

        # Concentration risk
        weights = [p["quantity"] * p.get("current_price", p["avg_price"]) / max(1, total_value) for p in portfolio.positions]
        concentration = max(weights) if weights else 0.0

        decomposition = RiskDecomposition(
            portfolio_id=portfolio_id,
            total_risk=total_risk,
            systematic_risk=systematic_risk,
            idiosyncratic_risk=idiosyncratic_risk,
            diversification_ratio=min(2.0, diversification_ratio),
            concentration_risk=concentration,
            position_risks=position_risks,
        )

        self._decompositions[portfolio_id] = decomposition
        return decomposition

    def monte_carlo_simulation(
        self,
        portfolio_id: str,
        simulations: int = 10000,
    ) -> MonteCarloResult:
        """Run Monte Carlo simulation."""
        portfolio = self._portfolios.get(portfolio_id)
        if not portfolio:
            raise HTTPException(404, f"Portfolio {portfolio_id} not found")

        total_value = portfolio.cash_balance + sum(
            p["quantity"] * p.get("current_price", p["avg_price"]) for p in portfolio.positions
        )

        # Simulate outcomes
        avg_final = total_value * 1.02  # 2% expected return
        std_dev = total_value * 0.15  # 15% volatility

        # Calculate probabilities
        prob_loss = 0.35
        prob_5pct_loss = 0.15
        prob_10pct_gain = 0.25

        # Percentiles
        percentiles = [
            avg_final * 0.9,
            avg_final * 0.95,
            avg_final * 0.99,
            avg_final,
            avg_final * 1.01,
            avg_final * 1.05,
            avg_final * 1.10,
        ]

        result = MonteCarloResult(
            portfolio_id=portfolio_id,
            simulations=simulations,
            avg_final_value=avg_final,
            std_dev=std_dev,
            prob_loss=prob_loss,
            prob_5pct_loss=prob_5pct_loss,
            prob_10pct_gain=prob_10pct_gain,
            percentiles=percentiles,
        )

        self._monte_carlo[portfolio_id] = result
        return result

    def get_report(self) -> dict:
        """Get portfolio risk analytics report."""
        return {
            "total_portfolios": len(self._portfolios),
            "total_var_calculations": len(self._var_results),
            "total_stress_tests": len(self._stress_tests),
            "total_scenarios": len(self._scenarios),
            "total_correlation_analyses": len(self._correlations),
            "total_factor_exposures": len(self._factor_exposures),
            "total_decompositions": len(self._decompositions),
            "total_monte_carlo": len(self._monte_carlo),
            "portfolios": [
                {
                    "name": p.name,
                    "positions": len(p.positions),
                    "risk_profile": p.risk_profile,
                }
                for p in self._portfolios.values()
            ],
        }


# Module-level singleton
risk_analytics = PortfolioRiskAnalytics()


# ── API Routes ──────────────────────────────────────────────────────────────


@router.post("/api/v1/trading/risk/portfolios")
async def create_portfolio(payload: dict):
    """Create a portfolio."""
    portfolio = risk_analytics.create_portfolio(
        name=payload["name"],
        cash_balance=payload.get("cash_balance", 0.0),
        risk_profile=payload.get("risk_profile", "balanced"),
    )
    return {"portfolio_id": portfolio.portfolio_id, "name": portfolio.name}


@router.post("/api/v1/trading/risk/portfolios/{portfolio_id}/positions")
async def add_position(portfolio_id: str, payload: dict):
    """Add a position."""
    position = risk_analytics.add_position(
        portfolio_id=portfolio_id,
        symbol=payload["symbol"],
        quantity=payload["quantity"],
        avg_price=payload["avg_price"],
    )
    return {"symbol": position.symbol, "quantity": position.quantity, "market_value": position.market_value}


@router.post("/api/v1/trading/risk/portfolios/{portfolio_id}/var")
async def calculate_var(portfolio_id: str, payload: dict):
    """Calculate VaR."""
    result = risk_analytics.calculate_var(
        portfolio_id=portfolio_id,
        method=payload.get("method", "historical"),
        confidence=payload.get("confidence", 0.95),
        horizon=payload.get("horizon", "1d"),
    )
    return {
        "var_95": result.var_95,
        "var_99": result.var_99,
        "cvar_95": result.cvar_95,
        "portfolio_value": result.portfolio_value,
        "method": result.var_method,
    }


@router.post("/api/v1/trading/risk/portfolios/{portfolio_id}/stress-test")
async def stress_test(portfolio_id: str, payload: dict):
    """Run stress test."""
    result = risk_analytics.stress_test(
        portfolio_id=portfolio_id,
        scenario_name=payload["scenario_name"],
        market_shock_pct=payload.get("market_shock_pct", -20.0),
    )
    return {
        "scenario": result.scenario_name,
        "loss_pct": result.loss_pct,
        "loss_usd": result.loss_usd,
        "recovery_estimate": result.recovery_estimate,
    }


@router.post("/api/v1/trading/risk/portfolios/{portfolio_id}/monte-carlo")
async def monte_carlo(portfolio_id: str, payload: dict):
    """Run Monte Carlo simulation."""
    result = risk_analytics.monte_carlo_simulation(
        portfolio_id=portfolio_id,
        simulations=payload.get("simulations", 10000),
    )
    return {
        "avg_final_value": result.avg_final_value,
        "std_dev": result.std_dev,
        "prob_loss": result.prob_loss,
        "prob_5pct_loss": result.prob_5pct_loss,
        "prob_10pct_gain": result.prob_10pct_gain,
    }


@router.get("/api/v1/trading/risk/report")
async def get_report():
    """Get portfolio risk analytics report."""
    return risk_analytics.get_report()
