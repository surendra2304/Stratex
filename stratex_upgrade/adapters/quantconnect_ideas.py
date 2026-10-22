"""LEAN-inspired buying-power and brokerage validation concepts."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True, slots=True)
class BuyingPowerResult:
    sufficient: bool
    remaining: Decimal
    reason: str = ""


class BuyingPowerGuard:
    def check(self, *, free_cash: Decimal, order_notional: Decimal, reserved: Decimal = Decimal(0)) -> BuyingPowerResult:
        remaining = free_cash - reserved - order_notional
        return BuyingPowerResult(remaining >= 0, max(Decimal(0), remaining), "OK" if remaining >= 0 else "INSUFFICIENT_BUYING_POWER")
