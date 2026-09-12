"""stratex_nautilus_adapter/risk.py

NautilusTrader-inspired Pre-Trade Institutional Risk Engine:
- Max order notional limit ($)
- Max instrument position notional limit ($)
- Max open orders limit
- High-frequency order velocity rate limiter (token bucket / sliding window)
- Drawdown circuit breaker (halts order routing if session drawdown exceeds threshold)
"""

from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass

from .models import NautilusOrder, RiskCheckResult, Position


@dataclass
class RiskConfig:
    """Institutional pre-trade risk thresholds."""
    max_order_notional_usd: float = 50000.0
    max_position_notional_usd: float = 200000.0
    max_open_orders: int = 50
    max_orders_per_second: int = 20
    max_drawdown_pct: float = 10.0  # Percentage (e.g., 10% halts trading)


class NautilusRiskEngine:
    """Pre-trade risk gateway validating orders against institutional limits."""

    def __init__(self, config: RiskConfig | None = None) -> None:
        self.config = config or RiskConfig()
        self._order_timestamps: deque[float] = deque()
        self._peak_equity: float = 100000.0
        self._current_equity: float = 100000.0
        self._is_halted: bool = False

    def update_equity(self, current_equity: float) -> None:
        """Updates equity tracking and checks drawdown circuit breaker."""
        self._current_equity = float(current_equity)
        if self._current_equity > self._peak_equity:
            self._peak_equity = self._current_equity

        if self._peak_equity > 0:
            drawdown_pct = ((self._peak_equity - self._current_equity) / self._peak_equity) * 100.0
            if drawdown_pct >= self.config.max_drawdown_pct:
                self._is_halted = True

    def resume_trading(self) -> None:
        """Manually resets circuit breaker halt."""
        self._is_halted = False

    def check_order(
        self,
        order: NautilusOrder,
        current_position: Position | None = None,
        open_orders_count: int = 0,
        estimated_price: float | None = None,
    ) -> RiskCheckResult:
        """Evaluates whether an order complies with all pre-trade institutional risk rules."""
        # 1. Circuit breaker check
        if self._is_halted:
            return RiskCheckResult(
                passed=False,
                reason="Trading halted by drawdown circuit breaker.",
                metric="CIRCUIT_BREAKER",
                value=self._current_equity,
                limit=self._peak_equity,
            )

        # 2. Estimate notional
        price = order.price or estimated_price or (order.stop_price if order.stop_price else 1.0)
        order_notional = order.quantity * price

        # 3. Max order notional check
        if order_notional > self.config.max_order_notional_usd:
            return RiskCheckResult(
                passed=False,
                reason=f"Order notional ${order_notional:,.2f} exceeds limit ${self.config.max_order_notional_usd:,.2f}.",
                metric="MAX_ORDER_NOTIONAL",
                value=order_notional,
                limit=self.config.max_order_notional_usd,
            )

        # 4. Max position notional check
        pos_qty = current_position.quantity if current_position else 0.0
        pos_price = current_position.avg_entry_price if current_position and current_position.avg_entry_price > 0 else price
        projected_qty = pos_qty + (order.quantity if order.side.value == "BUY" else -order.quantity)
        projected_notional = abs(projected_qty * pos_price)

        if projected_notional > self.config.max_position_notional_usd:
            return RiskCheckResult(
                passed=False,
                reason=f"Projected position notional ${projected_notional:,.2f} exceeds limit ${self.config.max_position_notional_usd:,.2f}.",
                metric="MAX_POSITION_NOTIONAL",
                value=projected_notional,
                limit=self.config.max_position_notional_usd,
            )

        # 5. Max open orders check
        if open_orders_count >= self.config.max_open_orders:
            return RiskCheckResult(
                passed=False,
                reason=f"Open orders count {open_orders_count} reached limit {self.config.max_open_orders}.",
                metric="MAX_OPEN_ORDERS",
                value=float(open_orders_count),
                limit=float(self.config.max_open_orders),
            )

        # 6. Rate limiter check (sliding 1-second window)
        now = time.monotonic()
        while self._order_timestamps and (now - self._order_timestamps[0]) > 1.0:
            self._order_timestamps.popleft()

        if len(self._order_timestamps) >= self.config.max_orders_per_second:
            return RiskCheckResult(
                passed=False,
                reason=f"Order rate {len(self._order_timestamps)}/sec exceeds limit {self.config.max_orders_per_second}/sec.",
                metric="RATE_LIMIT",
                value=float(len(self._order_timestamps)),
                limit=float(self.config.max_orders_per_second),
            )

        # Record this submission timestamp
        self._order_timestamps.append(now)

        return RiskCheckResult(passed=True, reason="OK")


# Default global risk engine singleton
default_risk_engine = NautilusRiskEngine()
