"""Exchange precision/limit helper.

CCXT normalizes market metadata; Stratex keeps final order validation authoritative.
"""

from __future__ import annotations
import math

class PrecisionHelper:
    @staticmethod
    def floor_step(value: float, step: float | None) -> float:
        if step is None or step <= 0:
            return float(value)
        return math.floor(value / step) * step

    @staticmethod
    def round_price(price: float, precision: int | None = None, step: float | None = None) -> float:
        if step is not None and step > 0:
            return PrecisionHelper.floor_step(price, step)
        if precision is not None:
            return round(price, precision)
        return float(price)

    @staticmethod
    def round_amount(amount: float, precision: int | None = None, step: float | None = None) -> float:
        if step is not None and step > 0:
            return PrecisionHelper.floor_step(amount, step)
        if precision is not None:
            return round(amount, precision)
        return float(amount)

    @staticmethod
    def validate_market_order(amount: float, price: float, market: dict) -> tuple[bool, str]:
        limits = market.get("limits") or {}
        amount_limits = limits.get("amount") or {}
        cost_limits = limits.get("cost") or {}

        minimum_amount = amount_limits.get("min")
        maximum_amount = amount_limits.get("max")
        minimum_cost = cost_limits.get("min")

        if minimum_amount is not None and amount < float(minimum_amount):
            return False, "AMOUNT_BELOW_MINIMUM"
        if maximum_amount is not None and amount > float(maximum_amount):
            return False, "AMOUNT_ABOVE_MAXIMUM"
        if minimum_cost is not None and amount * price < float(minimum_cost):
            return False, "NOTIONAL_BELOW_MINIMUM"
        return True, "PRECISION_LIMITS_OK"
