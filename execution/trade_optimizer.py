"""
Stratex/execution/trade_optimizer.py — Stratex Trade Optimizer.

Extends Stratex's capabilities with:
1. Multi-Strategy Portfolio Allocation: dynamically allocate across strategies
2. Real-Time Strategy Switching: switch between strategies based on market conditions
3. Risk Parity Allocation: risk-based position sizing
4. Walk-Forward Optimization: validate strategy on rolling windows
5. Strategy Ensemble: combine multiple strategies
6. Bayesian Strategy Selection: probabilistic strategy selection
7. Regime Detection: detect market regimes and adapt
"""
from __future__ import annotations

import hashlib
import math
import time
from dataclasses import dataclass, field
from typing import Any


@dataclass
class StrategyPerformance:
    """Track strategy performance."""
    strategy: str
    pnl: float = 0.0
    trades: int = 0
    wins: int = 0
    losses: int = 0
    win_rate: float = 0.0
    profit_factor: float = 0.0
    avg_win: float = 0.0
    avg_loss: float = 0.0
    max_drawdown: float = 0.0
    sharpe_ratio: float = 0.0
    sortino_ratio: float = 0.0
    volatility: float = 0.0
    last_updated: float = 0.0


@dataclass
class StrategyAllocation:
    """Allocation to a strategy."""
    strategy: str
    weight: float
    expected_return: float
    expected_risk: float
    contribution: float = 0.0


@dataclass
class MarketRegime:
    """Detected market regime."""
    regime: str  # bull, bear, sideways, volatile, crash
    confidence: float = 0.5
    indicators: dict[str, Any] = field(default_factory=dict)
    timestamp: float = 0.0
    suggested_strategies: list[str] = field(default_factory=list)


@dataclass
class EnsembleResult:
    """Result of strategy ensemble."""
    strategies: list[str]
    weights: list[float]
    expected_return: float
    expected_risk: float
    sharpe_ratio: float
    correlation_matrix: list[list[float]] = field(default_factory=list)


@dataclass
class WalkForwardResult:
    """Result of walk-forward optimization."""
    strategy: str
    in_sample_return: float
    out_of_sample_return: float
    efficiency_ratio: float
    roll_count: int
    avg_roll_return: float


@dataclass
class BayesianSelection:
    """Bayesian strategy selection."""
    strategy: str
    posterior_mean: float
    posterior_std: float
    probability_best: float
    upper_credible: float
    lower_credible: float


class TradeOptimizer:
    """Trade optimization engine."""

    def __init__(self):
        self._strategies: dict[str, StrategyPerformance] = {}
        self._regime_history: list[MarketRegime] = []
        self._ensemble_weights: dict[str, list[float]] = {}  # ensemble_id -> weights
        self._correlation_matrix: dict[str, list[list[float]]] = {}  # ensemble_id -> matrix

    # ── Strategy Performance Tracking ──────────────────────────────────────

    def record_trade(
        self,
        strategy: str,
        pnl: float,
        win: bool,
        risk: float = 0.0,
    ) -> StrategyPerformance:
        """Record a trade result."""
        if strategy not in self._strategies:
            self._strategies[strategy] = StrategyPerformance(strategy=strategy)

        sp = self._strategies[strategy]
        sp.pnl += pnl
        sp.trades += 1

        if win:
            sp.wins += 1
            sp.avg_win = (sp.avg_win * (sp.wins - 1) + pnl) / sp.wins
        else:
            sp.losses += 1
            sp.avg_loss = (sp.avg_loss * (sp.losses - 1) - pnl) / sp.losses

        sp.win_rate = sp.wins / max(1, sp.trades)
        sp.profit_factor = sp.avg_win / max(0.001, abs(sp.avg_loss))
        sp.volatility = max(sp.volatility, abs(pnl)) * 0.95
        sp.sharpe_ratio = (sp.avg_win / max(0.001, abs(sp.avg_loss)) - 1.0) * math.sqrt(sp.trades)
        sp.last_updated = time.time()

        return sp

    def get_strategy_performance(self) -> list[StrategyPerformance]:
        """Get all strategy performances."""
        return sorted(
            self._strategies.values(),
            key=lambda s: -s.sharpe_ratio
        )

    # ── Multi-Strategy Portfolio Allocation ────────────────────────────────

    def optimize_allocation(
        self,
        target_return: float = 0.1,
        max_risk: float = 0.2,
    ) -> list[StrategyAllocation]:
        """Optimize portfolio allocation across strategies."""
        strategies = self.get_strategy_performance()
        if not strategies:
            return []

        # Build return/risk matrix
        returns = [s.pnl / max(1, s.trades) for s in strategies]
        risks = [s.volatility / max(1, s.trades) for s in strategies]

        # Compute correlation matrix
        n = len(strategies)
        corr = self._compute_correlation(returns, risks)

        # Simple risk-parity allocation
        inv_risk = [1.0 / (r + 0.01) for r in risks]
        total_inv = sum(inv_risk)
        weights = [r / total_inv for r in inv_risk]

        # Adjust for target return
        avg_return = sum(w * r for w, r in zip(weights, returns))
        if avg_return < target_return:
            # Scale up high-return strategies
            for i, (w, r) in enumerate(zip(weights, returns)):
                if r > avg_return:
                    weights[i] *= 1.5
            # Renormalize
            total = sum(weights)
            weights = [w / total for w in weights]

        allocations = []
        for i, (strat, w, r, risk) in enumerate(zip(strategies, weights, returns, risks)):
            allocations.append(StrategyAllocation(
                strategy=strat.strategy,
                weight=w,
                expected_return=r,
                expected_risk=risk,
                contribution=w * r,
            ))

        return sorted(allocations, key=lambda a: -a.contribution)

    def _compute_correlation(self, returns: list[float], risks: list[float]) -> list[list[float]]:
        """Compute correlation matrix."""
        n = len(returns)
        corr = [[1.0 if i == j else 0.5 for j in range(n)] for i in range(n)]

        # Adjust based on volatility (more volatile -> less correlation assumed)
        for i in range(n):
            for j in range(n):
                if i != j:
                    corr[i][j] = 0.5 * (1.0 - min(0.8, (risks[i] + risks[j]) * 0.1))

        return corr

    # ── Real-Time Strategy Switching ───────────────────────────────────────

    def detect_market_regime(
        self,
        prices: list[float],
        volumes: list[float] | None = None,
    ) -> MarketRegime:
        """Detect current market regime."""
        if len(prices) < 20:
            return MarketRegime(regime="unknown", confidence=0.3)

        # Calculate indicators
        returns = [(prices[i] / prices[i-1] - 1) for i in range(1, len(prices))]
        volatility = self._stdev(returns)
        ma_fast = self._ma(prices, 10)
        ma_slow = self._ma(prices, 30)

        indicators = {
            "volatility": volatility,
            "ma_fast": ma_fast,
            "ma_slow": ma_slow,
            "trend": "up" if ma_fast > ma_slow else "down",
        }

        # Determine regime
        if volatility > 0.03:
            regime = "volatile"
        elif ma_fast > ma_slow and prices[-1] > prices[0]:
            regime = "bull"
        elif ma_fast < ma_slow and prices[-1] < prices[0]:
            regime = "bear"
        else:
            regime = "sideways"

        # Suggest strategies
        suggested = []
        if regime == "bull":
            suggested = ["trend_following", "momentum", "breakout"]
        elif regime == "bear":
            suggested = ["mean_reversion", "short_trend", "pairs_trading"]
        elif regime == "volatile":
            suggested = ["volatility_arbitrage", "range_trading", "options_strategies"]
        else:
            suggested = ["pairs_trading", "mean_reversion", "grid_trading"]

        regime_obj = MarketRegime(
            regime=regime,
            confidence=min(0.9, 0.5 + volatility * 10),
            indicators=indicators,
            timestamp=time.time(),
            suggested_strategies=suggested,
        )

        self._regime_history.append(regime_obj)
        return regime_obj

    def switch_strategy(
        self,
        current_strategy: str,
        regime: MarketRegime,
        strategies: list[StrategyPerformance],
    ) -> dict:
        """Switch to optimal strategy for current regime."""
        suggested = regime.suggested_strategies

        # Find best matching strategy
        best = None
        best_score = -1

        for strat in strategies:
            score = 0.0
            if strat.strategy in suggested:
                score += 0.4
            score += min(0.3, strat.sharpe_ratio * 0.1)
            score += min(0.3, strat.win_rate)

            if score > best_score:
                best_score = score
                best = strat

        return {
            "current": current_strategy,
            "recommended": best.strategy if best else current_strategy,
            "regime": regime.regime,
            "reason": f"Regime: {regime.regime}, best matching strategy",
            "confidence": regime.confidence,
        }

    def _ma(self, prices: list[float], window: int) -> float:
        return sum(prices[-window:]) / len(prices[-window:])

    def _stdev(self, values: list[float]) -> float:
        if len(values) < 2:
            return 0.0
        mean = sum(values) / len(values)
        variance = sum((v - mean) ** 2 for v in values) / len(values)
        return math.sqrt(variance)

    # ── Risk Parity Allocation ─────────────────────────────────────────────

    def risk_parity_allocation(
        self,
        strategies: list[str],
        risk_budget: float = 1.0,
    ) -> dict[str, float]:
        """Risk parity allocation across strategies."""
        # Calculate risk contributions
        risk_contributions = {}
        total_risk = 0.0

        for strat in strategies:
            sp = self._strategies.get(strat)
            if sp:
                risk = sp.volatility / max(1, sp.trades)
                risk_contributions[strat] = risk
                total_risk += risk

        # Allocate based on inverse risk
        allocation = {}
        total_inv = 0.0

        for strat in strategies:
            inv_risk = 1.0 / (risk_contributions.get(strat, 0.01) + 0.01)
            allocation[strat] = inv_risk
            total_inv += inv_risk

        # Normalize to risk budget
        for strat in strategies:
            allocation[strat] = (allocation[strat] / total_inv) * risk_budget

        return allocation

    # ── Walk-Forward Optimization ───────────────────────────────────────────

    def walk_forward_optimize(
        self,
        strategy: str,
        returns: list[float],
        train_window: int = 50,
        test_window: int = 20,
    ) -> WalkForwardResult:
        """Walk-forward optimization."""
        if len(returns) < train_window + test_window:
            return WalkForwardResult(strategy=strategy, roll_count=0)

        rolls = []
        start = 0

        while start + train_window + test_window <= len(returns):
            # In-sample
            is_data = returns[start:start + train_window]
            # Out-of-sample
            oos_data = returns[start + train_window:start + train_window + test_window]

            is_return = sum(is_data) / len(is_data)
            oos_return = sum(oos_data) / len(oos_data)

            rolls.append({
                "is": is_return,
                "oos": oos_return,
            })
            start += test_window

        if not rolls:
            return WalkForwardResult(strategy=strategy, roll_count=0)

        is_avg = sum(r["is"] for r in rolls) / len(rolls)
        oos_avg = sum(r["oos"] for r in rolls) / len(rolls)

        efficiency = abs(oos_avg) / max(0.001, abs(is_avg)) if is_avg != 0 else 0.0

        return WalkForwardResult(
            strategy=strategy,
            in_sample_return=is_avg,
            out_of_sample_return=oos_avg,
            efficiency_ratio=min(1.0, efficiency),
            roll_count=len(rolls),
            avg_roll_return=oos_avg,
        )

    # ── Strategy Ensemble ──────────────────────────────────────────────────

    def create_ensemble(
        self,
        strategies: list[str],
        method: str = "risk_parity",
    ) -> EnsembleResult:
        """Create strategy ensemble."""
        perf = [self._strategies[s] for s in strategies if s in self._strategies]
        if not perf:
            return EnsembleResult(strategies=[], weights=[])

        returns = [s.pnl / max(1, s.trades) for s in perf]
        risks = [s.volatility / max(1, s.trades) for s in perf]

        corr = self._compute_correlation(returns, risks)

        if method == "risk_parity":
            inv_risk = [1.0 / (r + 0.01) for r in risks]
            total = sum(inv_risk)
            weights = [r / total for r in inv_risk]
        elif method == "momentum":
            # Weight by sharpe ratio
            sharpes = [s.sharpe_ratio for s in perf]
            total = sum(sharpes)
            weights = [s / total if total > 0 else 1.0 / len(sharpes) for s in sharpes]
        else:
            # Equal weight
            weights = [1.0 / len(perf)] * len(perf)

        # Calculate ensemble return and risk
        ens_return = sum(w * r for w, r in zip(weights, returns))

        # Portfolio variance
        port_var = 0.0
        for i in range(len(weights)):
            for j in range(len(weights)):
                port_var += weights[i] * weights[j] * risks[i] * risks[j] * corr[i][j]

        ens_risk = math.sqrt(port_var) if port_var > 0 else 0.0

        ens_sharpe = ens_return / max(0.001, ens_risk) if ens_risk > 0 else 0.0

        ensemble_id = hashlib.md5(f"ensemble:{method}:{time.time()}".encode()).hexdigest()[:12]
        self._ensemble_weights[ensemble_id] = weights
        self._correlation_matrix[ensemble_id] = corr

        return EnsembleResult(
            strategies=[s.strategy for s in perf],
            weights=weights,
            expected_return=ens_return,
            expected_risk=ens_risk,
            sharpe_ratio=ens_sharpe,
            correlation_matrix=corr,
        )

    # ── Bayesian Strategy Selection ────────────────────────────────────────

    def bayesian_select(
        self,
        strategies: list[str],
        prior_mean: float = 0.0,
        prior_std: float = 1.0,
    ) -> list[BayesianSelection]:
        """Bayesian strategy selection."""
        selections = []

        for strat in strategies:
            sp = self._strategies.get(strat)
            if not sp or sp.trades < 5:
                continue

            # Posterior estimation
            mu = sp.pnl / max(1, sp.trades)
            sigma = sp.volatility / max(1, sp.trades)

            # Combine with prior
            posterior_mean = (mu + prior_mean) / 2
            posterior_std = math.sqrt((sigma**2 + prior_std**2) / 4)

            # Probability of being best (simplified)
            prob_best = sp.sharpe_ratio / sum(
                s.sharpe_ratio for s in self._strategies.values()
            ) if any(s.sharpe_ratio for s in self._strategies.values()) else 0.0

            selections.append(BayesianSelection(
                strategy=strat,
                posterior_mean=posterior_mean,
                posterior_std=posterior_std,
                probability_best=prob_best,
                upper_credible=posterior_mean + 1.96 * posterior_std,
                lower_credible=posterior_mean - 1.96 * posterior_std,
            ))

        return sorted(selections, key=lambda b: -b.probability_best)


# FastAPI Router
from fastapi import APIRouter, HTTPException

router = APIRouter()

optimizer = TradeOptimizer()


@router.post("/api/v1/trading/optimize/record")
async def record_trade(payload: dict):
    sp = optimizer.record_trade(
        strategy=payload["strategy"],
        pnl=payload["pnl"],
        win=payload["win"],
        risk=payload.get("risk", 0.0),
    )
    return sp.__dict__


@router.get("/api/v1/trading/optimize/performance")
async def strategy_performance():
    return [s.__dict__ for s in optimizer.get_strategy_performance()]


@router.get("/api/v1/trading/optimize/allocation")
async def optimize_allocation(
    target_return: float = 0.1,
    max_risk: float = 0.2,
):
    allocations = optimizer.optimize_allocation(target_return, max_risk)
    return [a.__dict__ for a in allocations]


@router.post("/api/v1/trading/optimize/regime")
async def detect_regime(payload: dict):
    regime = optimizer.detect_market_regime(
        payload["prices"],
        payload.get("volumes"),
    )
    return regime.__dict__


@router.post("/api/v1/trading/optimize/switch")
async def switch_strategy(payload: dict):
    regime = MarketRegime(
        regime=payload["regime"],
        confidence=payload.get("confidence", 0.5),
        suggested_strategies=payload.get("suggested_strategies", []),
    )
    result = optimizer.switch_strategy(
        payload["current_strategy"],
        regime,
        optimizer.get_strategy_performance(),
    )
    return result


@router.get("/api/v1/trading/optimize/risk_parity")
async def risk_parity(payload: dict):
    allocation = optimizer.risk_parity_allocation(
        payload["strategies"],
        payload.get("risk_budget", 1.0),
    )
    return {"allocation": allocation}


@router.post("/api/v1/trading/optimize/walk_forward")
async def walk_forward(payload: dict):
    result = optimizer.walk_forward_optimize(
        payload["strategy"],
        payload["returns"],
        payload.get("train_window", 50),
        payload.get("test_window", 20),
    )
    return result.__dict__


@router.post("/api/v1/trading/optimize/ensemble")
async def create_ensemble(payload: dict):
    result = optimizer.create_ensemble(
        payload["strategies"],
        payload.get("method", "risk_parity"),
    )
    return result.__dict__


@router.get("/api/v1/trading/optimize/bayesian")
async def bayesian_select():
    selections = optimizer.bayesian_select(list(optimizer._strategies.keys()))
    return [s.__dict__ for s in selections]
