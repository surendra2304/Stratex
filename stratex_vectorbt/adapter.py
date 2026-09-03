"""Stratex-native VectorBT-style research adapter.

VectorBT is used as an optional acceleration/research backend only. The canonical
Stratex BacktestEngine remains authoritative for final validation and promotion.
"""
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Mapping
import itertools
import math

@dataclass(frozen=True)
class SweepSpec:
    parameters: Mapping[str, Iterable[Any]]
    max_trials: int = 10_000
    seed: int = 42

@dataclass
class SweepResult:
    parameters: dict[str, Any]
    metrics: dict[str, float]
    accepted: bool = False
    rejection_reason: str | None = None

class VectorBTResearchAdapter:
    def __init__(self, backtest_fn: Callable[[Mapping[str, Any]], Mapping[str, float]]):
        self.backtest_fn = backtest_fn

    def generate_candidates(self, spec: SweepSpec):
        names = list(spec.parameters)
        values = [list(spec.parameters[n]) for n in names]
        count = 0
        for combo in itertools.product(*values):
            if count >= spec.max_trials:
                break
            count += 1
            yield dict(zip(names, combo))

    def sweep(self, spec: SweepSpec) -> list[SweepResult]:
        results: list[SweepResult] = []
        for params in self.generate_candidates(spec):
            raw = dict(self.backtest_fn(params))
            clean = {k: float(v) for k, v in raw.items() if isinstance(v, (int, float)) and math.isfinite(float(v))}
            results.append(SweepResult(params, clean))
        return results

    @staticmethod
    def rank(results: list[SweepResult], min_profit_factor: float = 1.20, max_drawdown: float | None = None):
        def score(r: SweepResult):
            pnl = r.metrics.get("net_pnl", 0.0)
            pf = r.metrics.get("profit_factor", 0.0)
            dd = abs(r.metrics.get("max_drawdown", 0.0))
            sharpe = r.metrics.get("sharpe", 0.0)
            r.accepted = pf >= min_profit_factor and (max_drawdown is None or dd <= max_drawdown)
            if not r.accepted:
                r.rejection_reason = "profitability_or_drawdown_gate"
            return (r.accepted, pnl, pf, sharpe, -dd)
        return sorted(results, key=score, reverse=True)

    @staticmethod
    def validate_with_canonical_engine(
        candidate: SweepResult,
        canonical_runner_fn: Callable[[Mapping[str, Any]], Mapping[str, float]],
    ) -> dict[str, Any]:
        """Validates shortlisted VectorBT candidate through Stratex's canonical BacktestEngine."""
        canonical_metrics = dict(canonical_runner_fn(candidate.parameters))
        is_confirmed = (
            canonical_metrics.get("profit_factor", 0.0) >= 1.10
            and canonical_metrics.get("net_pnl", 0.0) > 0.0
        )
        return {
            "parameters": candidate.parameters,
            "vectorbt_metrics": candidate.metrics,
            "canonical_metrics": canonical_metrics,
            "confirmed": is_confirmed,
        }

