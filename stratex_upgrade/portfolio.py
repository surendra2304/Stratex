from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True, slots=True)
class PortfolioSnapshot:
    equity: Decimal
    cash: Decimal
    gross_exposure: Decimal
    net_exposure: Decimal
    unrealized_pnl: Decimal
    realized_pnl: Decimal
    drawdown: Decimal


def snapshot(
    cash: Decimal,
    positions: Iterable[dict],
    marks: dict[str, Decimal],
    peak_equity: Decimal,
) -> PortfolioSnapshot:
    gross = Decimal(0)
    net = Decimal(0)
    unrealized = Decimal(0)
    realized = Decimal(0)
    for pos in positions:
        symbol = str(pos["symbol"])
        qty = Decimal(str(pos["quantity"]))
        entry = Decimal(str(pos["entry_price"]))
        side = str(pos.get("side", "LONG")).upper()
        mark = Decimal(str(marks.get(symbol, entry)))
        value = qty * mark
        gross += value
        if side in {"BUY", "LONG"}:
            net += value
            unrealized += (mark - entry) * qty
        else:
            net -= value
            unrealized += (entry - mark) * qty
        realized += Decimal(str(pos.get("realized_pnl", "0")))
    equity = cash + unrealized + realized
    dd = Decimal(0) if peak_equity <= 0 else max(Decimal(0), (peak_equity - equity) / peak_equity)
    return PortfolioSnapshot(equity, cash, gross, net, unrealized, realized, dd)
