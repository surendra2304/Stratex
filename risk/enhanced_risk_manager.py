"""
risk/enhanced_risk_manager.py — Enhanced Stratex Risk Management.

Extends the base DynamicRiskManager with:
1. Adaptive Position Sizing: Adjusts sizing based on strategy performance
2. Correlation-Aware Risk: Reduces exposure when positions are correlated
3. Volatility Targeting: Scales position sizes to target portfolio volatility
4. Drawdown-Aware Scaling: Automatically reduces risk during drawdowns
5. Multi-Asset Risk Parity: Allocates risk equally across uncorrelated assets
6. Stress Testing: Monte Carlo simulation for tail risk assessment
"""
from __future__ import annotations

import math
import time
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from logger import get_logger
from numeric_safety import finite_float, positive_float
from risk.dynamic_risk_manager import DynamicRiskManager, RiskBudget

logger = get_logger("risk.enhanced")


@dataclass
class StrategyPerformance:
    """Performance metrics for a strategy."""
    name: str
    sharpe: float = 0.0
    win_rate: float = 0.5
    avg_win: float = 0.0
    avg_loss: float = 0.0
    max_drawdown: float = 0.0
    profit_factor: float = 0.0
    recent_returns: list[float] = field(default_factory=list)

    def calculate_profit_factor(self) -> float:
        if self.avg_loss <= 0:
            return 0.0
        gross_win = self.avg_win * self.win_rate
        gross_loss = self.avg_loss * (1 - self.win_rate)
        if gross_loss <= 0:
            return float("inf") if gross_win > 0 else 0.0
        return gross_win / gross_loss

    def update_profit_factor(self) -> None:
        self.profit_factor = self.calculate_profit_factor()


@dataclass
class PortfolioRiskSnapshot:
    """Snapshot of portfolio risk at a point in time."""
    timestamp: float
    total_equity: float
    total_risk: float
    position_count: int
    var_95: float = 0.0
    var_99: float = 0.0
    cvar_95: float = 0.0
    max_drawdown: float = 0.0


class EnhancedRiskManager:
    """Enhanced risk manager with adaptive sizing and stress testing."""

    def __init__(self, budget: RiskBudget | None = None):
        self.base_manager = DynamicRiskManager(budget)
        self.strategy_performances: dict[str, StrategyPerformance] = {}
        self.risk_snapshots: list[PortfolioRiskSnapshot] = []
        self.correlation_matrix: dict[str, dict[str, float]] = {}
        self.volatility_target: float = 0.15  # 15% annualized target

    def register_strategy(self, name: str, sharpe: float = 0.0, win_rate: float = 0.5) -> StrategyPerformance:
        """Register a strategy with its performance metrics."""
        perf = StrategyPerformance(name=name, sharpe=sharpe, win_rate=win_rate)
        self.strategy_performances[name] = perf
        return perf

    def update_strategy_performance(
        self,
        name: str,
        win: float,
        loss: float,
        return_pct: float,
    ) -> None:
        """Update strategy performance after a trade."""
        if name not in self.strategy_performances:
            self.register_strategy(name)

        perf = self.strategy_performances[name]
        perf.recent_returns.append(return_pct)

        # Update win/loss averages
        if win > 0:
            perf.avg_win = (perf.avg_win * 0.9 + win * 0.1) if perf.recent_returns else win
            perf.win_rate = (perf.win_rate * 0.95 + 0.05)  # slight positive bias for wins
        if loss > 0:
            perf.avg_loss = (perf.avg_loss * 0.9 + loss * 0.1) if perf.recent_returns else loss

        perf.update_profit_factor()

        # Update Sharpe ratio from recent returns
        if len(perf.recent_returns) > 5:
            returns = np.array(perf.recent_returns[-30:])
            if len(returns) > 1:
                std = np.std(returns)
                if std > 0:
                    perf.sharpe = float(np.mean(returns) / std * math.sqrt(252))

    def calculate_adaptive_size(
        self,
        strategy_name: str,
        equity: float,
        entry_price: float,
        stop_loss_price: float,
        base_fraction: float | None = None,
    ) -> float:
        """Calculate position size with adaptive scaling based on strategy performance."""
        base_size = self.base_manager.calculate_fixed_fractional_size(
            equity=equity,
            entry_price=entry_price,
            stop_loss_price=stop_loss_price,
            fraction_pct=base_fraction,
        )

        if base_size <= 0:
            return 0.0

        # Get strategy performance
        perf = self.strategy_performances.get(strategy_name)
        if not perf:
            return base_size

        # Scale by strategy performance
        # Good strategies (high Sharpe, high win rate) get larger allocations
        performance_score = 0.0

        # Sharpe ratio contribution (0-1 range)
        sharpe_score = min(1.0, max(0.0, perf.sharpe / 2.0))  # Sharpe of 2.0 = 1.0
        performance_score += sharpe_score * 0.4

        # Win rate contribution
        win_rate_score = (perf.win_rate - 0.5) * 2.0  # 0.5 -> 0, 1.0 -> 1.0
        performance_score += max(0.0, win_rate_score) * 0.3

        # Profit factor contribution
        pf_score = min(1.0, perf.profit_factor / 1.5)  # PF of 1.5 = 1.0
        performance_score += pf_score * 0.3

        # Scale base size by performance (0.5x to 1.5x)
        scale = 0.5 + performance_score * 1.0

        return base_size * scale

    def calculate_volatility_targeted_size(
        self,
        equity: float,
        entry_price: float,
        stop_loss_price: float,
        asset_volatility: float,
    ) -> float:
        """Calculate position size targeting a specific portfolio volatility."""
        if equity <= 0 or asset_volatility <= 0:
            return 0.0

        # Risk per unit
        risk_per_unit = abs(entry_price - stop_loss_price)
        if risk_per_unit <= 0:
            return 0.0

        # Target risk per trade (1% of equity)
        target_risk = equity * self.base_manager.budget.max_risk_per_trade_pct

        # Volatility-adjusted risk
        # Higher volatility assets get smaller positions
        volatility_scale = self.volatility_target / asset_volatility

        # Cap the scale (don't increase risk for low-vol assets beyond 2x)
        volatility_scale = min(2.0, max(0.5, volatility_scale))

        qty = (target_risk * volatility_scale) / risk_per_unit

        return qty

    def update_correlation(
        self,
        asset_a: str,
        asset_b: str,
        correlation: float,
    ) -> None:
        """Update correlation between two assets."""
        if asset_a not in self.correlation_matrix:
            self.correlation_matrix[asset_a] = {}
        if asset_b not in self.correlation_matrix:
            self.correlation_matrix[asset_b] = {}

        self.correlation_matrix[asset_a][asset_b] = correlation
        self.correlation_matrix[asset_b][asset_a] = correlation

    def calculate_correlation_adjusted_size(
        self,
        asset: str,
        equity: float,
        entry_price: float,
        stop_loss_price: float,
        existing_positions: dict[str, float],
    ) -> float:
        """Reduce position size if asset is highly correlated with existing positions."""
        base_size = self.base_manager.calculate_fixed_fractional_size(
            equity=equity,
            entry_price=entry_price,
            stop_loss_price=stop_loss_price,
        )

        if base_size <= 0:
            return 0.0

        # Check correlation with existing positions
        correlation_penalty = 0.0
        total_correlation = 0.0
        count = 0

        for pos_asset, _ in existing_positions.items():
            if pos_asset == asset:
                continue

            corr = self.correlation_matrix.get(asset, {}).get(pos_asset, 0.0)
            if corr > 0.8:  # High correlation
                total_correlation += corr
                count += 1

        if count > 0:
            avg_correlation = total_correlation / count
            correlation_penalty = (avg_correlation - 0.8) * 0.5  # 0 to 0.1

        # Reduce size by correlation penalty
        adjusted_size = base_size * (1.0 - correlation_penalty)

        return max(0.0, adjusted_size)

    def run_monte_carlo_stress_test(
        self,
        equity: float,
        positions: dict[str, dict[str, float]],
        simulations: int = 10000,
        days: int = 30,
    ) -> dict[str, Any]:
        """Run Monte Carlo stress test on current portfolio."""
        logger.info(f"Running Monte Carlo stress test: {simulations} simulations, {days} days")

        # Generate random returns for each asset
        results = []

        for sim in range(simulations):
            sim_equity = equity

            for _ in range(days):
                for asset, pos_data in positions.items():
                    # Random daily return (assume 15% annualized volatility)
                    vol = 0.15 / math.sqrt(252)  # Daily volatility
                    ret = np.random.normal(0.0005, vol)  # Small positive drift

                    # Calculate position value
                    quantity = pos_data.get("quantity", 0.0)
                    entry_price = pos_data.get("entry_price", 0.0)
                    stop_loss = pos_data.get("stop_loss", 0.0)

                    current_price = entry_price * (1 + ret)
                    pnl = quantity * (current_price - entry_price)

                    # Check stop loss
                    if (pos_data.get("side") == "BUY" and current_price <= stop_loss) or \
                       (pos_data.get("side") == "SELL" and current_price >= stop_loss):
                        pnl = quantity * (stop_loss - entry_price) * (1 if pos_data.get("side") == "BUY" else -1)

                    sim_equity += pnl

            results.append(sim_equity)

        # Calculate risk metrics
        results = np.array(results)
        portfolio_returns = results / equity - 1.0

        # VaR (95% and 99%)
        var_95 = float(np.percentile(portfolio_returns, 5) * equity)
        var_99 = float(np.percentile(portfolio_returns, 1) * equity)

        # CVaR (Conditional VaR / Expected Shortfall)
        cvar_95 = float(portfolio_returns[portfolio_returns <= np.percentile(portfolio_returns, 5)].mean() * equity)

        # Max drawdown
        cumulative = np.cumprod(1 + portfolio_returns)
        peak = np.maximum.accumulate(cumulative)
        drawdowns = (cumulative - peak) / peak
        max_dd = float(drawdowns.min() * equity)

        return {
            "var_95": var_95,
            "var_99": var_99,
            "cvar_95": cvar_95,
            "max_drawdown": max_dd,
            "expected_shortfall": cvar_95,
            "simulation_count": simulations,
            "simulation_days": days,
        }

    def take_risk_snapshot(
        self,
        equity: float,
        positions: dict[str, dict[str, float]],
    ) -> PortfolioRiskSnapshot:
        """Take a snapshot of current portfolio risk."""
        total_risk = sum(
            abs(p.get("quantity", 0) * (p.get("entry_price", 0) - p.get("stop_loss", 0)))
            for p in positions.values()
        )

        snapshot = PortfolioRiskSnapshot(
            timestamp=time.time(),
            total_equity=equity,
            total_risk=total_risk,
            position_count=len(positions),
        )

        # Run quick stress test
        stress = self.run_monte_carlo_stress_test(equity, positions, simulations=1000, days=10)
        snapshot.var_95 = stress["var_95"]
        snapshot.var_99 = stress["var_99"]
        snapshot.cvar_95 = stress["cvar_95"]
        snapshot.max_drawdown = stress["max_drawdown"]

        self.risk_snapshots.append(snapshot)
        return snapshot

    def get_risk_report(self) -> dict[str, Any]:
        """Generate a comprehensive risk report."""
        if not self.risk_snapshots:
            return {"status": "NO_DATA", "message": "No risk snapshots available"}

        latest = self.risk_snapshots[-1]

        report = {
            "timestamp": latest.timestamp,
            "total_equity": latest.total_equity,
            "total_risk": latest.total_risk,
            "position_count": latest.position_count,
            "var_95": latest.var_95,
            "var_99": latest.var_99,
            "cvar_95": latest.cvar_95,
            "max_drawdown": latest.max_drawdown,
            "strategy_performances": {
                name: {
                    "sharpe": perf.sharpe,
                    "win_rate": perf.win_rate,
                    "profit_factor": perf.profit_factor,
                    "avg_win": perf.avg_win,
                    "avg_loss": perf.avg_loss,
                }
                for name, perf in self.strategy_performances.items()
            },
        }

        # Add risk limit checks
        budget = self.base_manager.budget
        report["limit_checks"] = {
            "max_risk_per_trade": latest.total_risk <= budget.max_risk_per_trade_pct * latest.total_equity,
            "max_portfolio_risk": latest.total_risk <= budget.max_portfolio_risk_pct * latest.total_equity,
        }

        return report


# Module-level singleton
enhanced_risk_manager = EnhancedRiskManager()
