from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True, slots=True)
class CostModel:
    taker_fee_bps: Decimal = Decimal(10)
    maker_fee_bps: Decimal = Decimal(6)
    fixed_fee_quote: Decimal = Decimal(0)
    base_slippage_bps: Decimal = Decimal(5)
    impact_coefficient_bps_per_1x_adv: Decimal = Decimal(10)

    def fee(self, notional: Decimal, liquidity: str = "taker") -> Decimal:
        bps = self.maker_fee_bps if liquidity.lower() == "maker" else self.taker_fee_bps
        return notional * bps / Decimal(10000) + self.fixed_fee_quote

    def slippage_bps(self, spread_bps: Decimal | None, participation_rate: Decimal = Decimal(0)) -> Decimal:
        spread = max(Decimal(0), spread_bps or Decimal(0))
        participation = max(Decimal(0), participation_rate)
        impact = self.impact_coefficient_bps_per_1x_adv * participation
        return max(self.base_slippage_bps, spread / Decimal(2)) + impact

    def buy_fill_price(self, reference: Decimal, slippage_bps: Decimal) -> Decimal:
        return reference * (Decimal(1) + slippage_bps / Decimal(10000))

    def sell_fill_price(self, reference: Decimal, slippage_bps: Decimal) -> Decimal:
        return reference * (Decimal(1) - slippage_bps / Decimal(10000))


def net_pnl(
    side: str,
    entry: Decimal,
    exit: Decimal,
    quantity: Decimal,
    entry_fee: Decimal,
    exit_fee: Decimal,
) -> Decimal:
    if side.upper() == "BUY":
        gross = (exit - entry) * quantity
    else:
        gross = (entry - exit) * quantity
    return gross - entry_fee - exit_fee
