"""Walk-forward validation for Stratex."""

from dataclasses import dataclass
from typing import Callable, Sequence, Any

@dataclass
class Window:
    train_start: int
    train_end: int
    test_start: int
    test_end: int

class WalkForwardValidator:
    def __init__(self, train_size: int, test_size: int, step_size: int | None = None):
        if train_size <= 0 or test_size <= 0:
            raise ValueError("train_size and test_size must be positive")
        if step_size is not None and step_size <= 0:
            raise ValueError("step_size must be positive")
        self.train_size = train_size
        self.test_size = test_size
        self.step_size = step_size or test_size


    def windows(self, n: int) -> list[Window]:
        out = []
        start = 0
        while start + self.train_size + self.test_size <= n:
            out.append(Window(
                train_start=start,
                train_end=start + self.train_size,
                test_start=start + self.train_size,
                test_end=start + self.train_size + self.test_size,
            ))
            start += self.step_size
        return out

    def run(
        self,
        data: Sequence[Any],
        fit_fn: Callable[[Sequence[Any]], dict],
        test_fn: Callable[[Sequence[Any], dict], dict],
    ) -> list[dict]:
        results = []
        for w in self.windows(len(data)):
            train = data[w.train_start:w.train_end]
            test = data[w.test_start:w.test_end]
            params = fit_fn(train)
            metrics = test_fn(test, params)
            results.append({
                "window": w.__dict__,
                "params": params,
                "metrics": metrics,
            })
        return results
