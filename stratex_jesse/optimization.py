from dataclasses import dataclass
from typing import Any, Callable, Sequence

@dataclass(frozen=True)
class HyperParameter:
    name: str
    kind: str
    minimum: Any | None = None
    maximum: Any | None = None
    options: Sequence[Any] | None = None
    default: Any | None = None

@dataclass(frozen=True)
class OptimizationSplit:
    train_start: str
    train_end: str
    test_start: str
    test_end: str

class JesseOptimizationAdapter:
    """Train/test optimizer contract inspired by Jesse's optimization workflow."""
    def __init__(self, evaluate_fn: Callable[[dict[str, Any], tuple[str, str]], dict[str, float]]):
        self.evaluate_fn = evaluate_fn

    def run_candidate(self, params: dict[str, Any], split: OptimizationSplit):
        train = self.evaluate_fn(params, (split.train_start, split.train_end))
        test = self.evaluate_fn(params, (split.test_start, split.test_end))
        return {"parameters": params, "train": train, "test": test}

    @staticmethod
    def degradation(train: dict[str, float], test: dict[str, float], metric: str = "sharpe"):
        a = float(train.get(metric, 0.0))
        b = float(test.get(metric, 0.0))
        if a == 0:
            return 0.0
        return (a - b) / abs(a)
