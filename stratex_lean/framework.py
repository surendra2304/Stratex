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
