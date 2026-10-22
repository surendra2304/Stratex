from __future__ import annotations
from dataclasses import dataclass
from typing import Callable, Sequence


@dataclass(frozen=True)
class Insight:
    symbol: str
    direction: int
    confidence: float
    magnitude: float = 1.0

@dataclass(frozen=True)
class PortfolioTarget:
    symbol: str
    target_weight: float

class AlphaRiskExecutionPipeline:
    """LEAN-inspired modular pipeline; Stratex gates remain authoritative."""
    def __init__(self, alpha: Callable[..., Sequence[Insight]], portfolio: Callable[[Sequence[Insight]], Sequence[PortfolioTarget]], risk: Callable[[Sequence[PortfolioTarget]], Sequence[PortfolioTarget]], execution: Callable[[Sequence[PortfolioTarget]], None]):
        self.alpha = alpha
        self.portfolio = portfolio
        self.risk = risk
        self.execution = execution

    def step(self, *args, **kwargs):
        insights = self.alpha(*args, **kwargs)
        targets = self.portfolio(insights)
        targets = self.risk(targets)
        self.execution(targets)
        return list(targets)

    @classmethod
    def with_portfolio_optimizer(
        cls,
        alpha: Callable[..., Sequence[Insight]],
        execution: Callable[[Sequence[PortfolioTarget]], None],
        max_weight: float = 0.35,
        max_gross: float = 1.0,
    ) -> AlphaRiskExecutionPipeline:
        """Constructs a standard pipeline powered by PortfolioOptimizer and PortfolioRiskOverlay."""
        from stratex_more_integrations.risk_overlay import PortfolioRiskOverlay
        import pandas as pd

        overlay = PortfolioRiskOverlay(max_single_weight=max_weight, max_gross=max_gross)

        def portfolio_builder(insights: Sequence[Insight]) -> Sequence[PortfolioTarget]:
            if not insights:
                return []
            total_conf = sum(ins.confidence for ins in insights) or 1.0
            targets = []
            for ins in insights:
                wt = (ins.confidence / total_conf) * max_gross * ins.direction
                targets.append(PortfolioTarget(symbol=ins.symbol, target_weight=wt))
            return targets

        def risk_manager(targets: Sequence[PortfolioTarget]) -> Sequence[PortfolioTarget]:
            if not targets:
                return []
            weights = pd.Series({t.symbol: t.target_weight for t in targets})
            adj_weights, _ = overlay.apply(weights)
            return [PortfolioTarget(symbol=s, target_weight=float(w)) for s, w in adj_weights.items()]

        return cls(alpha=alpha, portfolio=portfolio_builder, risk=risk_manager, execution=execution)

