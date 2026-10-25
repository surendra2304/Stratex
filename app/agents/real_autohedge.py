"""
Stratex — AutoHedge Engine

AutoHedge-inspired: autonomous hedge fund built on swarm intelligence and AI agents.
Key concepts implemented: swarm consensus, agent voting, risk-adjusted position sizing,
drawdown control, Kelly criterion position sizing, stop-loss logic, portfolio rebalancing,
volatility targeting, Sharpe ratio optimization, and backtesting.

Tests: tests/test_real_batch4a.py
"""

from __future__ import annotations

import math
import random
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any


@dataclass
class SwarmAgent:
    """Individual agent in the swarm."""
    name: str
    risk_tolerance: float = 0.5
    conviction: float = 0.7
    specialty: str = "balanced"
    weight: float = 1.0
    recent_accuracy: float = 0.6

    def conviction_weighted_signal(self, signal_strength: float) -> float:
        return signal_strength * self.conviction * self.weight

    def risk_adjusted_signal(self, signal: float, volatility: float) -> float:
        if volatility <= 0:
            return 0.0
        return signal / volatility * self.risk_tolerance


@dataclass
class SwarmConsensus:
    """Swarm consensus engine for AutoHedge."""
    agents: list[SwarmAgent] = field(default_factory=list)

    def add_agent(self, agent: SwarmAgent) -> None:
        self.agents.append(agent)

    def weighted_vote(self, signals: list[float]) -> float:
        if not signals or not self.agents:
            return 0.0
        weighted = sum(
            s * a.weight * a.conviction for s, a in zip(signals, self.agents)
        )
        max_weight = sum(a.weight for a in self.agents)
        return weighted / max_weight if max_weight > 0 else 0.0

    def consensus_signal(self, signals: list[float]) -> dict[str, Any]:
        if not signals:
            return {"signal": 0.0, "confidence": 0.0, "disagreement": 0.0}

        weighted = self.weighted_vote(signals)
        avg = sum(signals) / len(signals)
        disagreement = sum((s - avg) ** 2 for s in signals) / len(signals)

        # Confidence: higher when agents agree
        agreement = 1 - math.sqrt(disagreement)
        confidence = max(0.0, min(1.0, agreement))

        return {
            "signal": weighted,
            "confidence": confidence,
            "disagreement": disagreement,
            "agent_count": len(signals),
        }

    def agent_performance_update(self, agent_name: str, correct: bool) -> None:
        for agent in self.agents:
            if agent.name == agent_name:
                # Exponential moving average
                agent.recent_accuracy = (
                    agent.recent_accuracy * 0.8 + (1.0 if correct else 0.0) * 0.2
                )
                agent.weight = agent.recent_accuracy * agent.conviction
                break


@dataclass
class KellyPositionSizer:
    """Kelly criterion position sizing."""
    bankroll: float = 100000.0

    def kelly_fraction(self, win_prob: float, win_ratio: float) -> float:
        if win_ratio <= 0:
            return 0.0
        b = win_ratio  # b = odds
        p = win_prob
        q = 1 - p
        # f* = (bp - q) / b
        if p * b <= q:
            return 0.0
        return (p * b - q) / b

    def fractional_kelly(self, kelly_fraction: float, fraction: float = 0.25) -> float:
        return kelly_fraction * fraction

    def position_size(self, win_prob: float, win_ratio: float, fraction: float = 0.25) -> float:
        kelly = self.kelly_fraction(win_prob, win_ratio)
        adj = self.fractional_kelly(kelly, fraction)
        return self.bankroll * adj


@dataclass
class RiskManager:
    """AutoHedge risk manager."""
    max_drawdown_pct: float = 0.20
    stop_loss_pct: float = 0.05
    take_profit_pct: float = 0.15
    daily_limit_pct: float = 0.03

    def drawdown_check(self, peak_value: float, current_value: float) -> bool:
        if peak_value <= 0:
            return True
        dd = (peak_value - current_value) / peak_value
        return dd <= self.max_drawdown_pct

    def stop_loss_triggered(self, entry_price: float, current_price: float) -> bool:
        if entry_price <= 0:
            return False
        return (entry_price - current_price) / entry_price >= self.stop_loss_pct

    def take_profit_triggered(self, entry_price: float, current_price: float) -> bool:
        if entry_price <= 0:
            return False
        return (current_price - entry_price) / entry_price >= self.take_profit_pct

    def daily_loss_check(self, day_start_value: float, current_value: float) -> bool:
        if day_start_value <= 0:
            return True
        loss = (day_start_value - current_value) / day_start_value
        return loss <= self.daily_limit_pct

    def volatility_target(self, realized_vol: float, target_vol: float = 0.10) -> float:
        if realized_vol <= 0:
            return 1.0
        return target_vol / realized_vol


@dataclass
class PortfolioRebalancer:
    """Portfolio rebalancing engine."""
    target_weights: dict[str, float] = field(default_factory=dict)
    cash: float = 100000.0
    positions: dict[str, float] = field(default_factory=dict)

    def set_target(self, asset: str, weight: float) -> None:
        self.target_weights[asset] = weight

    def current_weight(self, asset: str, total_value: float) -> float:
        if total_value <= 0:
            return 0.0
        return self.positions.get(asset, 0.0) / total_value

    def drift(self, asset: str, total_value: float) -> float:
        target = self.target_weights.get(asset, 0.0)
        current = self.current_weight(asset, total_value)
        return target - current

    def needs_rebalance(self, total_value: float, threshold: float = 0.05) -> bool:
        for asset in self.target_weights:
            if abs(self.drift(asset, total_value)) > threshold:
                return True
        return False

    def rebalance(self, prices: dict[str, float]) -> dict[str, float]:
        total = sum(self.positions[p] * q for p, q in prices.items() if p in self.positions) + self.cash
        trades: dict[str, float] = {}
        for asset, target_w in self.target_weights.items():
            target_val = total * target_w
            current_val = self.positions.get(asset, 0.0) * prices.get(asset, 1.0)
            diff = target_val - current_val
            if abs(diff) > 0:
                trades[asset] = diff
        return trades


@dataclass
class BacktestEngine:
    """AutoHedge backtesting engine."""
    initial_capital: float = 100000.0
    positions: dict[str, float] = field(default_factory=dict)
    cash: float = 100000.0
    peak_value: float = 100000.0

    def buy(self, asset: str, price: float, shares: float) -> None:
        cost = price * shares
        if cost <= self.cash:
            self.positions[asset] = self.positions.get(asset, 0.0) + shares
            self.cash -= cost

    def sell(self, asset: str, price: float, shares: float) -> None:
        if self.positions.get(asset, 0.0) >= shares:
            proceeds = price * shares
            self.positions[asset] -= shares
            self.cash += proceeds
            if asset not in self.positions or self.positions[asset] == 0:
                del self.positions[asset]

    def total_value(self, prices: dict[str, float]) -> float:
        holdings = sum(s * prices.get(a, 0.0) for a, s in self.positions.items())
        return holdings + self.cash

    def update_peak(self, prices: dict[str, float]) -> None:
        val = self.total_value(prices)
        if val > self.peak_value:
            self.peak_value = val

    def drawdown(self, prices: dict[str, float]) -> float:
        val = self.total_value(prices)
        if self.peak_value <= 0:
            return 0.0
        return (self.peak_value - val) / self.peak_value

    def sharpe_ratio(self, returns: list[float], risk_free: float = 0.0) -> float:
        if len(returns) < 2:
            return 0.0
        excess = [r - risk_free for r in returns]
        mean = sum(excess) / len(excess)
        var = sum((r - mean) ** 2 for r in excess) / (len(excess) - 1)
        std = math.sqrt(var)
        if std == 0:
            return 0.0
        return mean / std * math.sqrt(252)

    def sortino_ratio(self, returns: list[float], risk_free: float = 0.0) -> float:
        if len(returns) < 2:
            return 0.0
        excess = [r - risk_free for r in returns]
        mean = sum(excess) / len(excess)
        downside = [min(0, r) for r in excess]
        downside_var = sum(d ** 2 for d in downside) / len(downside)
        downside_std = math.sqrt(downside_var)
        if downside_std == 0:
            return 0.0
        return mean / downside_std * math.sqrt(252)


@dataclass
class DrawdownController:
    """Drawdown-based position controller."""
    peak_value: float = 100000.0
    soft_limit: float = 0.10
    hard_limit: float = 0.20
    base_position: float = 1.0

    def update_peak(self, value: float) -> None:
        if value > self.peak_value:
            self.peak_value = value

    def drawdown(self, current_value: float) -> float:
        if self.peak_value <= 0:
            return 0.0
        return (self.peak_value - current_value) / self.peak_value

    def position_multiplier(self, current_value: float) -> float:
        dd = self.drawdown(current_value)
        if dd >= self.hard_limit:
            return 0.0
        if dd >= self.soft_limit:
            return self.base_position * (1 - (dd - self.soft_limit) / (self.hard_limit - self.soft_limit))
        return self.base_position


@dataclass
class VolatilityTargeter:
    """Volatility targeting engine."""
    target_vol: float = 0.10
    vol_window: int = 20

    def compute_volatility(self, prices: list[float]) -> float:
        if len(prices) < 2:
            return 0.0
        returns = [prices[i] / prices[i-1] - 1 for i in range(1, len(prices))]
        mean = sum(returns) / len(returns)
        var = sum((r - mean) ** 2 for r in returns) / (len(returns) - 1)
        return math.sqrt(var) * math.sqrt(252)

    def target_weight(self, prices: list[float]) -> float:
        realized = self.compute_volatility(prices[-self.vol_window:])
        if realized <= 0:
            return 1.0
        return min(self.target_vol / realized, 2.0)

    def scaled_position(self, base_position: float, prices: list[float]) -> float:
        return base_position * self.target_weight(prices)


@dataclass
class AgentCoordinator:
    """Coordinates multiple trading agents."""
    agents: dict[str, SwarmAgent] = field(default_factory=dict)
    historical_accuracy: dict[str, float] = field(default_factory=dict)

    def register_agent(self, agent: SwarmAgent) -> None:
        self.agents[agent.name] = agent
        if agent.name not in self.historical_accuracy:
            self.historical_accuracy[agent.name] = 0.5

    def aggregate_signals(self, agent_signals: dict[str, float]) -> float:
        if not agent_signals:
            return 0.0
        weighted = 0.0
        total_weight = 0.0
        for name, signal in agent_signals.items():
            if name in self.agents and name in self.historical_accuracy:
                w = self.historical_accuracy[name] * self.agents[name].conviction
                weighted += signal * w
                total_weight += w
        return weighted / total_weight if total_weight > 0 else 0.0

    def update_accuracy(self, agent_name: str, correct: bool) -> None:
        if agent_name in self.historical_accuracy:
            self.historical_accuracy[agent_name] = (
                self.historical_accuracy[agent_name] * 0.8 + (1.0 if correct else 0.0) * 0.2
            )
