"""Fail-closed pre-trade risk checks for the local Nautilus-inspired simulator."""

from __future__ import annotations

import math
import time
from collections import deque
from dataclasses import dataclass
from numbers import Integral, Real
from threading import RLock

from .models import (
    NautilusOrder,
    OrderSide,
    OrderType,
    Position,
    RiskCheckResult,
)


def _finite_number(value: object, *, positive: bool = False, non_negative: bool = False) -> bool:
    """Return whether value is a finite real scalar meeting the requested bound."""
    if isinstance(value, bool) or not isinstance(value, Real):
        return False
    number = float(value)
    if not math.isfinite(number):
        return False
    if positive and number <= 0.0:
        return False
    if non_negative and number < 0.0:
        return False
    return True


@dataclass(frozen=True)
class RiskConfig:
    """Immutable pre-trade risk thresholds.

    A non-finite or invalid threshold can silently disable ordinary comparisons,
    so configuration is validated at construction rather than during trading.
    """

    max_order_notional_usd: float = 50000.0
    max_position_notional_usd: float = 200000.0
    max_open_orders: int = 50
    max_orders_per_second: int = 20
    max_drawdown_pct: float = 10.0

    def __post_init__(self) -> None:
        for field_name in ("max_order_notional_usd", "max_position_notional_usd", "max_drawdown_pct"):
            value = getattr(self, field_name)
            if not _finite_number(value, positive=True):
                raise ValueError(f"{field_name} must be a finite positive number")

        for field_name in ("max_open_orders", "max_orders_per_second"):
            value = getattr(self, field_name)
            if isinstance(value, bool) or not isinstance(value, Integral) or value < 1:
                raise ValueError(f"{field_name} must be a positive integer")


class NautilusRiskEngine:
    """Thread-safe pre-trade risk gateway.

    Orders without a defensible price estimate are rejected. This engine never
    substitutes a made-up price merely to allow the notional calculation to run.
    """

    def __init__(self, config: RiskConfig | None = None) -> None:
        if config is not None and not isinstance(config, RiskConfig):
            raise TypeError("config must be a RiskConfig instance or None")
        self.config = config if config is not None else RiskConfig()
        self._order_timestamps: deque[float] = deque()
        self._peak_equity: float = 100000.0
        self._current_equity: float = 100000.0
        self._is_halted = False
        self._lock = RLock()

    @staticmethod
    def _deny(
        reason: str,
        metric: str = "INVALID_INPUT",
        value: float = 0.0,
        limit: float = 0.0,
    ) -> RiskCheckResult:
        return RiskCheckResult(passed=False, reason=reason, metric=metric, value=value, limit=limit)

    def update_equity(self, current_equity: float) -> None:
        """Update equity and trigger the circuit breaker for excessive drawdown.

        Invalid telemetry halts new orders before reporting the input error. The
        last known-good equity snapshot is retained so status output stays finite.
        """
        with self._lock:
            if not _finite_number(current_equity):
                self._is_halted = True
                raise ValueError("current_equity must be a finite real number")

            equity = float(current_equity)
            self._current_equity = equity
            if equity > self._peak_equity:
                self._peak_equity = equity

            if self._peak_equity > 0.0:
                drawdown_pct = ((self._peak_equity - equity) / self._peak_equity) * 100.0
                if drawdown_pct >= self.config.max_drawdown_pct:
                    self._is_halted = True

    def resume_trading(self) -> None:
        """Manually clear the circuit-breaker halt after operator review."""
        with self._lock:
            self._is_halted = False

    def check_order(
        self,
        order: NautilusOrder,
        current_position: Position | None = None,
        open_orders_count: int = 0,
        estimated_price: float | None = None,
    ) -> RiskCheckResult:
        """Evaluate order exposure, projected position, queue limits and rate.

        The lock covers the complete check so a concurrent equity update or a burst
        of submissions cannot race the halt/rate-limit decision.
        """
        with self._lock:
            if self._is_halted:
                return RiskCheckResult(
                    passed=False,
                    reason="Trading halted by drawdown circuit breaker.",
                    metric="CIRCUIT_BREAKER",
                    value=self._current_equity,
                    limit=self._peak_equity,
                )

            if not isinstance(order, NautilusOrder):
                return self._deny("order must be a NautilusOrder instance")
            if not isinstance(order.symbol, str) or not order.symbol.strip():
                return self._deny("order symbol must be a non-empty string")
            if not isinstance(order.side, OrderSide):
                return self._deny("order side must be BUY or SELL")
            if not isinstance(order.order_type, OrderType):
                return self._deny("order type is invalid")
            if not _finite_number(order.quantity, positive=True):
                return self._deny("order quantity must be a finite positive number")
            if isinstance(open_orders_count, bool) or not isinstance(open_orders_count, Integral) or open_orders_count < 0:
                return self._deny("open_orders_count must be a non-negative integer")

            # Select the first semantically valid source without using truthiness:
            # an explicit zero/NaN order price must not be silently replaced.
            if order.price is not None:
                price = order.price
            elif estimated_price is not None:
                price = estimated_price
            elif order.order_type in (OrderType.STOP_MARKET, OrderType.STOP_LIMIT, OrderType.TRAILING_STOP):
                price = order.stop_price
            else:
                price = None

            if price is None:
                return self._deny(
                    "A finite positive price or estimated_price is required for pre-trade risk.",
                    metric="PRICE_UNAVAILABLE",
                )
            if not _finite_number(price, positive=True):
                return self._deny("risk price must be a finite positive number")

            order_notional = float(order.quantity) * float(price)
            if not math.isfinite(order_notional):
                return self._deny("order notional overflowed and cannot be risk-checked", metric="NOTIONAL_OVERFLOW")
            if order_notional > self.config.max_order_notional_usd:
                return RiskCheckResult(
                    passed=False,
                    reason=(
                        f"Order notional ${order_notional:,.2f} exceeds limit "
                        f"${self.config.max_order_notional_usd:,.2f}."
                    ),
                    metric="MAX_ORDER_NOTIONAL",
                    value=order_notional,
                    limit=self.config.max_order_notional_usd,
                )

            pos_qty = 0.0
            if current_position is not None:
                if not isinstance(current_position, Position):
                    return self._deny("current_position must be a Position instance or None")
                if (
                    not isinstance(current_position.symbol, str)
                    or current_position.symbol.strip().upper() != order.symbol.strip().upper()
                ):
                    return self._deny("current_position symbol must match the order symbol")
                if not _finite_number(current_position.quantity):
                    return self._deny("position quantity must be a finite number")
                if not _finite_number(current_position.avg_entry_price, non_negative=True):
                    return self._deny("position average entry price must be finite and non-negative")
                pos_qty = float(current_position.quantity)
                if pos_qty != 0.0 and current_position.avg_entry_price <= 0.0:
                    return self._deny("a non-flat position must have a positive average entry price")

            signed_order_qty = float(order.quantity) if order.side is OrderSide.BUY else -float(order.quantity)
            projected_qty = pos_qty + signed_order_qty
            if not math.isfinite(projected_qty):
                return self._deny("projected position quantity overflowed", metric="POSITION_OVERFLOW")
            projected_notional = abs(projected_qty * float(price))
            if not math.isfinite(projected_notional):
                return self._deny("projected position notional overflowed", metric="POSITION_OVERFLOW")
            if projected_notional > self.config.max_position_notional_usd:
                return RiskCheckResult(
                    passed=False,
                    reason=(
                        f"Projected position notional ${projected_notional:,.2f} exceeds limit "
                        f"${self.config.max_position_notional_usd:,.2f}."
                    ),
                    metric="MAX_POSITION_NOTIONAL",
                    value=projected_notional,
                    limit=self.config.max_position_notional_usd,
                )

            if open_orders_count >= self.config.max_open_orders:
                return RiskCheckResult(
                    passed=False,
                    reason=(
                        f"Open orders count {open_orders_count} reached limit "
                        f"{self.config.max_open_orders}."
                    ),
                    metric="MAX_OPEN_ORDERS",
                    value=float(open_orders_count),
                    limit=float(self.config.max_open_orders),
                )

            now = time.monotonic()
            while self._order_timestamps and now - self._order_timestamps[0] >= 1.0:
                self._order_timestamps.popleft()

            if len(self._order_timestamps) >= self.config.max_orders_per_second:
                return RiskCheckResult(
                    passed=False,
                    reason=(
                        f"Order rate {len(self._order_timestamps)}/sec exceeds limit "
                        f"{self.config.max_orders_per_second}/sec."
                    ),
                    metric="RATE_LIMIT",
                    value=float(len(self._order_timestamps)),
                    limit=float(self.config.max_orders_per_second),
                )

            self._order_timestamps.append(now)
            return RiskCheckResult(passed=True, reason="OK")


# Default global risk engine singleton used by the facade.
default_risk_engine = NautilusRiskEngine()
