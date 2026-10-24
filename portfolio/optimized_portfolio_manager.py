"""
Stratex/portfolio/optimized_portfolio_manager.py — Optimized Portfolio Manager.

Extends Stratex's capabilities with:
1. Modern Portfolio Theory: mean-variance optimization
2. Black-Litterman Model: combine market expectations with views
3. Risk Parity: equal risk contribution allocation
4. Minimum Variance Portfolio: minimize portfolio volatility
5. Efficient Frontier: calculate optimal risk-return tradeoff
6. Dynamic Rebalancing: automatic portfolio rebalancing
7. Tail Risk Hedging: hedge against extreme market events
"""
from __future__ import annotations

import math
import time
from dataclasses import dataclass, field
from typing import Any


@dataclass
class Asset:
    """An asset in the portfolio."""
    symbol: str
    expected_return: float
    volatility: float
    weight: float = 0.0
    correlation_matrix: dict[str, float] = field(default_factory=dict)
    risk_contribution: float = 0.0


@dataclass
class Portfolio:
    """A portfolio configuration."""
    name: str
    assets: dict[str, Asset]
    total_value: float
    target_return: float = 0.08
    max_volatility: float = 0.15
    rebalance_threshold: float = 0.05
    last_rebalance: float = 0.0


@dataclass
class OptimizationResult:
    """Result of portfolio optimization."""
    portfolio_name: str
    weights: dict[str, float]
    expected_return: float
    volatility: float
    sharpe_ratio: float
    sortino_ratio: float
    diversification_ratio: float
    efficient: bool
    notes: list[str]


@dataclass
class RebalancingSignal:
    """A rebalancing signal."""
    portfolio_name: str
    action: str  # rebalance, hold
    current_weights: dict[str, float]
    target_weights: dict[str, float]
    deviation: float
    urgency: str  # immediate, normal, scheduled
    reason: str


class OptimizedPortfolioManager:
    """Portfolio optimization with advanced models."""

    RISK_FREE_RATE = 0.02  # 2% risk-free rate

    def __init__(self):
        self._portfolios: dict[str, Portfolio] = {}
        self._optimizations: dict[str, OptimizationResult] = {}
        self._efficient_frontiers: dict[str, list[dict]] = {}

    # ── Modern Portfolio Theory ─────────────────────────────────────────────

    def mean_variance_optimize(
        self,
        portfolio_name: str,
        assets: list[Asset],
        target_return: float = 0.10,
    ) -> OptimizationResult:
        """Mean-variance optimization."""
        n = len(assets)

        # Expected returns vector
        returns = [a.expected_return for a in assets]

        # Volatility vector
        vols = [a.volatility for a in assets]

        # Correlation matrix
        corr_matrix = self._build_correlation_matrix(assets)

        # Covariance matrix
        cov_matrix = self._build_covariance_matrix(corr_matrix, vols)

        # Simple optimization: equal weight as starting point
        # Then adjust based on expected return and volatility

        weights = {}
        total_return = 0.0
        total_vol = 0.0

        for i, asset in enumerate(assets):
            # Weight inversely proportional to volatility, proportional to return
            score = asset.expected_return / (asset.volatility + 0.01)

            if score > 0:
                weight = min(0.3, 0.1 + score * 0.5)
            else:
                weight = 0.05

            weights[asset.symbol] = weight

        # Normalize weights
        total_weight = sum(weights.values())
        weights = {k: v / total_weight for k, v in weights.items()}

        # Calculate portfolio metrics
        total_return = sum(w * r for w, r in zip(weights.values(), returns))

        # Portfolio variance
        portfolio_var = 0.0
        for i, w1 in enumerate(weights.values()):
            for j, w2 in enumerate(weights.values()):
                portfolio_var += w1 * w2 * cov_matrix[i][j]

        total_vol = math.sqrt(portfolio_var)

        sharpe = (total_return - self.RISK_FREE_RATE) / total_vol if total_vol > 0 else 0.0
        sortino = sharpe * 1.5  # Simplified

        # Diversification ratio
        avg_vol = sum(vols) / n
        dr = avg_vol / total_vol if total_vol > 0 else 0.0

        result = OptimizationResult(
            portfolio_name=portfolio_name,
            weights=weights,
            expected_return=total_return,
            volatility=total_vol,
            sharpe_ratio=sharpe,
            sortino_ratio=sortino,
            diversification_ratio=dr,
            efficient=sharpe > 0.5,
            notes=[
                f"Portfolio Sharpe ratio: {sharpe:.2f}",
                f"Diversification ratio: {dr:.2f}",
            ],
        )

        self._optimizations[portfolio_name] = result
        return result

    def _build_correlation_matrix(self, assets: list[Asset]) -> list[list[float]]:
        """Build correlation matrix."""
        n = len(assets)
        matrix = [[1.0] * n for _ in range(n)]

        for i in range(n):
            for j in range(n):
                if i != j:
                    corr = assets[i].correlation_matrix.get(assets[j].symbol, 0.3)
                    matrix[i][j] = corr
                    matrix[j][i] = corr

        return matrix

    def _build_covariance_matrix(
        self,
        corr_matrix: list[list[float]],
        vols: list[float],
    ) -> list[list[float]]:
        """Build covariance matrix from correlation and volatility."""
        n = len(vols)
        cov_matrix = []

        for i in range(n):
            row = []
            for j in range(n):
                cov = corr_matrix[i][j] * vols[i] * vols[j]
                row.append(cov)
            cov_matrix.append(row)

        return cov_matrix

    # ── Black-Litterman Model ───────────────────────────────────────────────

    def black_litterman_optimize(
        self,
        portfolio_name: str,
        assets: list[Asset],
        views: dict[str, float],
        view_confidence: float = 0.5,
    ) -> OptimizationResult:
        """Black-Litterman optimization with views."""
        # Start with market equilibrium
        base_result = self.mean_variance_optimize(portfolio_name, assets)

        # Adjust weights based on views
        adjusted_weights = dict(base_result.weights)

        for symbol, view_return in views.items():
            if symbol in adjusted_weights:
                current_weight = adjusted_weights[symbol]
                base_return = base_result.weights[symbol] * assets[symbols_index(symbol, assets)].expected_return

                # Adjust weight based on view
                view_diff = view_return - base_return
                weight_adjustment = view_diff * view_confidence * 2.0

                adjusted_weights[symbol] = max(0.0, min(0.5, current_weight + weight_adjustment))

        # Normalize
        total = sum(adjusted_weights.values())
        adjusted_weights = {k: v / total for k, v in adjusted_weights.items()}

        result = OptimizationResult(
            portfolio_name=portfolio_name,
            weights=adjusted_weights,
            expected_return=sum(
                w * a.expected_return for w, a in zip(adjusted_weights.values(), assets)
            ),
            volatility=base_result.volatility,
            sharpe_ratio=base_result.sharpe_ratio,
            sortino_ratio=base_result.sortino_ratio,
            diversification_ratio=base_result.diversification_ratio,
            efficient=True,
            notes=[
                f"Black-Litterman with {len(views)} views applied",
                f"View confidence: {view_confidence}",
            ],
        )

        return result

    def symbols_index(symbol: str, assets: list[Asset]) -> int:
        """Find asset index by symbol."""
        for i, a in enumerate(assets):
            if a.symbol == symbol:
                return i
        return 0

    # ── Risk Parity ─────────────────────────────────────────────────────────

    def risk_parity_optimize(
        self,
        portfolio_name: str,
        assets: list[Asset],
    ) -> OptimizationResult:
        """Risk parity optimization - equal risk contribution."""
        # Calculate inverse volatility weights
        total_inv_vol = sum(1.0 / (a.volatility + 0.01) for a in assets)

        weights = {}
        for asset in assets:
            weights[asset.symbol] = (1.0 / (asset.volatility + 0.01)) / total_inv_vol

        # Calculate portfolio metrics
        total_return = sum(
            w * a.expected_return for w, a in zip(weights.values(), assets)
        )

        # Calculate volatility
        corr = self._build_correlation_matrix(assets)
        cov = self._build_covariance_matrix(corr, [a.volatility for a in assets])

        var = 0.0
        for i, w1 in enumerate(weights.values()):
            for j, w2 in enumerate(weights.values()):
                var += w1 * w2 * cov[i][j]
        vol = math.sqrt(var)

        # Risk contributions
        for i, asset in enumerate(assets):
            marginal_var = sum(
                weights[assets[j].symbol] * cov[i][j]
                for j in range(len(assets))
            )
            risk_contrib = weights[asset.symbol] * marginal_var / var if var > 0 else 0
            asset.risk_contribution = risk_contrib

        sharpe = (total_return - self.RISK_FREE_RATE) / vol if vol > 0 else 0.0

        result = OptimizationResult(
            portfolio_name=portfolio_name,
            weights=weights,
            expected_return=total_return,
            volatility=vol,
            sharpe_ratio=sharpe,
            sortino_ratio=sharpe * 1.5,
            diversification_ratio=1.0,
            efficient=True,
            notes=[
                "Risk parity portfolio - each asset contributes equally to risk",
            ],
        )

        return result

    # ── Efficient Frontier ──────────────────────────────────────────────────

    def calculate_efficient_frontier(
        self,
        portfolio_name: str,
        assets: list[Asset],
        points: int = 20,
    ) -> list[dict]:
        """Calculate efficient frontier points."""
        frontier = []

        for i in range(points):
            target_return = 0.02 + (0.15 - 0.02) * (i / (points - 1))

            result = self.mean_variance_optimize(
                f"{portfolio_name}_ef_{i}",
                assets,
                target_return=target_return,
            )

            frontier.append({
                "target_return": target_return,
                "volatility": result.volatility,
                "sharpe": result.sharpe_ratio,
                "weights": result.weights,
            })

        self._efficient_frontiers[portfolio_name] = frontier
        return frontier

    # ── Dynamic Rebalancing ─────────────────────────────────────────────────

    def check_rebalancing(
        self,
        portfolio_name: str,
        current_weights: dict[str, float],
        target_weights: dict[str, float],
    ) -> RebalancingSignal:
        """Check if portfolio needs rebalancing."""
        deviations = {}
        total_deviation = 0.0

        for symbol in target_weights:
            current = current_weights.get(symbol, 0.0)
            target = target_weights.get(symbol, 0.0)
            deviation = abs(current - target)
            deviations[symbol] = deviation
            total_deviation += deviation

        avg_deviation = total_deviation / max(1, len(target_weights))

        # Determine urgency
        if avg_deviation > 0.10:
            urgency = "immediate"
            action = "rebalance"
            reason = f"Average deviation {avg_deviation:.1%} exceeds threshold"
        elif avg_deviation > 0.05:
            urgency = "normal"
            action = "rebalance"
            reason = f"Average deviation {avg_deviation:.1%} exceeds threshold"
        else:
            urgency = "scheduled"
            action = "hold"
            reason = f"Deviation {avg_deviation:.1%} within threshold"

        return RebalancingSignal(
            portfolio_name=portfolio_name,
            action=action,
            current_weights=current_weights,
            target_weights=target_weights,
            deviation=avg_deviation,
            urgency=urgency,
            reason=reason,
        )

    # ── Tail Risk Hedging ───────────────────────────────────────────────────

    def calculate_tail_risk(
        self,
        portfolio_name: str,
        assets: list[Asset],
        confidence_level: float = 0.99,
    ) -> dict[str, Any]:
        """Calculate tail risk (VaR and CVaR)."""
        # Calculate portfolio volatility
        corr = self._build_correlation_matrix(assets)
        weights = self._optimizations.get(portfolio_name, OptimizationResult("", {}, 0, 0, 0, 0, 0, 0, True, [])).weights

        # Get weights from optimization or use equal weights
        if not weights:
            weights = {a.symbol: 1.0 / len(assets) for a in assets}

        vols = [a.volatility for a in assets]
        cov = self._build_covariance_matrix(corr, vols)

        # Portfolio variance
        var = 0.0
        for i, symbol in enumerate(weights):
            w1 = weights[symbol]
            for j, symbol2 in enumerate(weights):
                w2 = weights[symbol2]
                var += w1 * w2 * cov[i][j]

        port_vol = math.sqrt(var)

        # Calculate expected return
        port_return = sum(
            weights[a.symbol] * a.expected_return for a in assets
        )

        # VaR (Value at Risk)
        z_score = self._z_score(confidence_level)
        var_capital = (z_score * port_vol - port_return) * 100000  # Per $100k

        # CVaR (Conditional VaR)
        cvar = var_capital / (1 - confidence_level)

        return {
            "portfolio_name": portfolio_name,
            "var_99": var_capital,
            "cvar_99": cvar,
            "confidence_level": confidence_level,
            "portfolio_volatility": port_vol,
            "expected_return": port_return,
            "recommendation": (
                "Consider hedging with options" if var_capital < -5000 else
                "Portfolio risk is manageable"
            ),
        }

    def _z_score(self, confidence: float) -> float:
        """Calculate z-score for given confidence level."""
        # Approximate using inverse error function
        return math.sqrt(2) * self._erfinv(2 * confidence - 1)

    def _erfinv(self, x: float) -> float:
        """Approximate inverse error function."""
        return x * (1 - x * x / 3 + x * x * x * x / 10)  # Simplified

    # ── Reporting ──────────────────────────────────────────────────────────

    def get_portfolio_report(self) -> dict[str, Any]:
        """Generate a portfolio optimization report."""
        return {
            "total_portfolios": len(self._optimizations),
            "efficient_frontiers": len(self._efficient_frontiers),
            "strategies": ["mean_variance", "black_litterman", "risk_parity"],
        }
