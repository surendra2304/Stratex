from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True, slots=True)
class ValidationGate:
    min_forward_days: int = 30
    min_forward_trades: int = 30
    min_profit_factor: Decimal = Decimal("1.20")
    min_expectancy: Decimal = Decimal(0)
    max_drawdown: Decimal = Decimal("0.05")
    min_positive_folds_ratio: Decimal = Decimal("0.60")


@dataclass(frozen=True, slots=True)
class ValidationResult:
    approved: bool
    reasons: tuple[str, ...]
    profit_factor: Decimal
    expectancy: Decimal
    drawdown: Decimal
    forward_days: int
    forward_trades: int
    positive_folds_ratio: Decimal


def evaluate_validation(
    pnl: Sequence[Decimal],
    *,
    forward_days: int,
    positive_folds: int,
    total_folds: int,
    gate: ValidationGate | None = None,
) -> ValidationResult:
    gate = gate or ValidationGate()
    pnl = list(pnl)
    gains = sum((x for x in pnl if x > 0), Decimal(0))
    losses = -sum((x for x in pnl if x < 0), Decimal(0))
    pf = gains / losses if losses > 0 else (Decimal(999) if gains > 0 else Decimal(0))
    expectancy = sum(pnl, Decimal(0)) / Decimal(str(len(pnl))) if pnl else Decimal(0)
    equity = Decimal(0)
    peak = Decimal(0)
    max_dd = Decimal(0)
    for x in pnl:
        equity += x
        peak = max(peak, equity)
        max_dd = max(max_dd, peak - equity)
    base = max(Decimal(1), peak)
    dd = max_dd / base
    fold_ratio = Decimal(str(positive_folds / total_folds)) if total_folds else Decimal(0)
    reasons = []
    if forward_days < gate.min_forward_days:
        reasons.append("INSUFFICIENT_FORWARD_DAYS")
    if len(pnl) < gate.min_forward_trades:
        reasons.append("INSUFFICIENT_FORWARD_TRADES")
    if pf < gate.min_profit_factor:
        reasons.append("PROFIT_FACTOR_BELOW_GATE")
    if expectancy <= gate.min_expectancy:
        reasons.append("NON_POSITIVE_EXPECTANCY")
    if dd > gate.max_drawdown:
        reasons.append("MAX_DRAWDOWN_EXCEEDED")
    if fold_ratio < gate.min_positive_folds_ratio:
        reasons.append("INSUFFICIENT_POSITIVE_FOLDS")
    return ValidationResult(
        approved=not reasons,
        reasons=tuple(reasons),
        profit_factor=pf,
        expectancy=expectancy,
        drawdown=dd,
        forward_days=forward_days,
        forward_trades=len(pnl),
        positive_folds_ratio=fold_ratio,
    )


def strategy_status(result: ValidationResult) -> str:
    return "VALIDATED" if result.approved else "OBSERVE_ONLY"
