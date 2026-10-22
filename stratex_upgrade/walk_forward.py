from __future__ import annotations

import statistics
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import TypeVar

T = TypeVar("T")


@dataclass(frozen=True, slots=True)
class Window:
    train_start: int
    train_end: int
    test_start: int
    test_end: int


@dataclass(frozen=True, slots=True)
class FoldMetrics:
    fold: int
    pnl: Decimal
    trades: int
    win_rate: float
    profit_factor: float
    max_drawdown: float


@dataclass(frozen=True, slots=True)
class WalkForwardResult:
    folds: tuple[FoldMetrics, ...]
    aggregate_pnl: Decimal
    median_profit_factor: float
    positive_folds: int
    total_folds: int


def tiled_windows(n: int, train: int, test: int, step: int | None = None) -> list[Window]:
    if min(n, train, test) <= 0:
        raise ValueError("window parameters must be positive")
    step = step or test
    windows: list[Window] = []
    start = 0
    while start + train + test <= n:
        windows.append(Window(start, start + train, start + train, start + train + test))
        start += step
    return windows


def summarize_fold(fold: int, pnls: Sequence[Decimal]) -> FoldMetrics:
    total = sum(pnls, Decimal(0))
    wins = [p for p in pnls if p > 0]
    losses = [-p for p in pnls if p < 0]
    pf = float(sum(wins, Decimal(0)) / sum(losses, Decimal(0))) if losses else (float("inf") if wins else 0.0)
    equity = Decimal(0)
    peak = Decimal(0)
    max_dd = Decimal(0)
    for p in pnls:
        equity += p
        peak = max(peak, equity)
        max_dd = max(max_dd, peak - equity)
    base = float(max(Decimal(1), peak + (Decimal(1) if peak == 0 else Decimal(0))))
    dd = float(max_dd / Decimal(str(base)))
    return FoldMetrics(fold, total, len(pnls), len(wins) / len(pnls) if pnls else 0.0, pf, dd)


def evaluate_folds(fold_pnls: Sequence[Sequence[Decimal]]) -> WalkForwardResult:
    metrics = tuple(summarize_fold(i, pnls) for i, pnls in enumerate(fold_pnls, 1))
    agg = sum((m.pnl for m in metrics), Decimal(0))
    pfs = [m.profit_factor for m in metrics if m.profit_factor != float("inf")]
    median_pf = statistics.median(pfs) if pfs else (float("inf") if metrics else 0.0)
    positive = sum(1 for m in metrics if m.pnl > 0)
    return WalkForwardResult(metrics, agg, median_pf, positive, len(metrics))
