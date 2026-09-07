from __future__ import annotations

import math
from collections.abc import Sequence
from decimal import Decimal


def profit_factor(pnls: Sequence[Decimal]) -> Decimal:
    gains = sum((p for p in pnls if p > 0), Decimal(0))
    losses = -sum((p for p in pnls if p < 0), Decimal(0))
    if losses == 0:
        return Decimal(999) if gains > 0 else Decimal(0)
    return gains / losses


def expectancy(pnls: Sequence[Decimal]) -> Decimal:
    return sum(pnls, Decimal(0)) / Decimal(str(len(pnls))) if pnls else Decimal(0)


def max_drawdown(equity: Sequence[Decimal]) -> Decimal:
    peak = None
    max_dd = Decimal(0)
    for value in equity:
        peak = value if peak is None else max(peak, value)
        if peak > 0:
            max_dd = max(max_dd, (peak - value) / peak)
    return max_dd


def sharpe(pnls: Sequence[float], periods_per_year: float = 252.0) -> float:
    if len(pnls) < 2:
        return 0.0
    mean = sum(pnls) / len(pnls)
    variance = sum((x - mean) ** 2 for x in pnls) / (len(pnls) - 1)
    std = math.sqrt(variance)
    return 0.0 if std == 0 else mean / std * math.sqrt(periods_per_year)


def sortino(pnls: Sequence[float], periods_per_year: float = 252.0) -> float:
    if not pnls:
        return 0.0
    mean = sum(pnls) / len(pnls)
    downside = [min(0.0, x) for x in pnls]
    denom = math.sqrt(sum(x * x for x in downside) / len(downside))
    return 0.0 if denom == 0 else mean / denom * math.sqrt(periods_per_year)
