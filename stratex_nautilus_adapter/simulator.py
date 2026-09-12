"""stratex_nautilus_adapter/simulator.py

Deterministic Event-Driven Execution Simulator inspired by NautilusTrader:
- Matching engine supporting Market, Limit, Stop, and Bracket orders
- Configurable slippage models: Zero, Fixed Bps, and Square-Root Market Impact
- Maker/Taker commission modeling
- Real-time Position, PnL, and FillReport tracking
"""

from __future__ import annotations

import math
import uuid
from datetime import datetime, timezone
from enum import Enum
from dataclasses import dataclass

from .models import (
    NautilusOrder,
    OrderSide,
    OrderType,
    OrderState,
    TradeTick,
    FillReport,
    Position,
    LiquidityRole,
)
from .orders import NautilusOrderManager


class SlippageModel(str, Enum):
    ZERO = "ZERO"
    FIXED_BPS = "FIXED_BPS"
    MARKET_IMPACT = "MARKET_IMPACT"


@dataclass
class SimulatorConfig:
    """Configuration parameters for execution simulation."""
    slippage_model: SlippageModel = SlippageModel.FIXED_BPS
    fixed_slippage_bps: float = 2.0  # 2 basis points
    impact_constant: float = 0.05
    taker_fee_pct: float = 0.0005    # 5 bps
    maker_fee_pct: float = 0.0002    # 2 bps
    latency_ms: float = 5.0          # 5 ms simulated round-trip


class NautilusExecutionSimulator:
    """Simulates realistic matching engine execution with friction and position tracking."""

    def __init__(
        self,
        config: SimulatorConfig | None = None,
        order_manager: NautilusOrderManager | None = None,
    ) -> None:
        self.config = config or SimulatorConfig()
        self.order_manager = order_manager or NautilusOrderManager()
        self._positions: dict[str, Position] = {}
        self._fills: list[FillReport] = []

    def get_position(self, symbol: str) -> Position:
        """Retrieves current position for symbol."""
        sym_upper = symbol.upper()
        if sym_upper not in self._positions:
            self._positions[sym_upper] = Position(symbol=sym_upper)
        return self._positions[sym_upper]

    def list_fills(self, symbol: str | None = None) -> list[FillReport]:
        """Lists execution fill reports."""
        if symbol:
            sym_upper = symbol.upper()
            return [f for f in self._fills if f.symbol == sym_upper]
        return list(self._fills)

    def calculate_slippage(
        self,
        base_price: float,
        side: OrderSide,
        quantity: float,
        liquidity_volume: float = 100.0,
    ) -> float:
        """Calculates effective fill price including configured slippage model."""
        if self.config.slippage_model == SlippageModel.ZERO:
            return base_price

        sign = 1.0 if side == OrderSide.BUY else -1.0

        if self.config.slippage_model == SlippageModel.FIXED_BPS:
            slippage_fraction = (self.config.fixed_slippage_bps / 10000.0)
            return base_price * (1.0 + (sign * slippage_fraction))

        if self.config.slippage_model == SlippageModel.MARKET_IMPACT:
            vol_ratio = quantity / max(liquidity_volume, quantity)
            impact_fraction = (self.config.fixed_slippage_bps / 10000.0) + (
                self.config.impact_constant * math.sqrt(vol_ratio) * 0.01
            )
            return base_price * (1.0 + (sign * impact_fraction))

        return base_price

    def process_tick(self, tick: TradeTick) -> list[FillReport]:
        """Matches open orders against incoming market tick."""
        open_orders = self.order_manager.list_orders(symbol=tick.symbol, is_open=True)
        new_fills: list[FillReport] = []

        for order in open_orders:
            # Auto-accept submitted orders in simulation
            if order.state == OrderState.SUBMITTED:
                order = self.order_manager.update_order_state(order.client_order_id, OrderState.ACCEPTED)

            can_fill = False
            is_maker = False
            fill_price = tick.price

            if order.order_type == OrderType.MARKET:
                can_fill = True
                is_maker = False
                fill_price = self.calculate_slippage(tick.price, order.side, order.quantity, tick.size)

            elif order.order_type == OrderType.LIMIT and order.price is not None:
                if order.side == OrderSide.BUY and tick.price <= order.price:
                    can_fill = True
                    is_maker = True
                    fill_price = order.price
                elif order.side == OrderSide.SELL and tick.price >= order.price:
                    can_fill = True
                    is_maker = True
                    fill_price = order.price

            elif order.order_type == OrderType.STOP_MARKET and order.stop_price is not None:
                if order.side == OrderSide.BUY and tick.price >= order.stop_price:
                    can_fill = True
                    is_maker = False
                    fill_price = self.calculate_slippage(tick.price, order.side, order.quantity, tick.size)
                elif order.side == OrderSide.SELL and tick.price <= order.stop_price:
                    can_fill = True
                    is_maker = False
                    fill_price = self.calculate_slippage(tick.price, order.side, order.quantity, tick.size)

            if can_fill:
                qty = order.remaining_qty
                fee_rate = self.config.maker_fee_pct if is_maker else self.config.taker_fee_pct
                commission = (fill_price * qty) * fee_rate

                fill = FillReport(
                    fill_id=f"fill_{uuid.uuid4().hex[:10]}",
                    client_order_id=order.client_order_id,
                    symbol=order.symbol,
                    price=fill_price,
                    quantity=qty,
                    commission=commission,
                    liquidity_role=LiquidityRole.MAKER if is_maker else LiquidityRole.TAKER,
                    timestamp=datetime.now(timezone.utc),
                )
                self._fills.append(fill)
                new_fills.append(fill)

                # Transition order state to FILLED
                self.order_manager.update_order_state(
                    order.client_order_id,
                    new_state=OrderState.FILLED,
                    fill_qty=qty,
                    fill_price=fill_price,
                )

                # Update Position
                self._update_position(order.symbol, order.side, qty, fill_price)

        return new_fills

    def _update_position(
        self,
        symbol: str,
        side: OrderSide,
        qty: float,
        price: float,
    ) -> Position:
        pos = self.get_position(symbol)
        now = datetime.now(timezone.utc)

        delta_qty = qty if side == OrderSide.BUY else -qty
        new_qty = pos.quantity + delta_qty

        # Realized PnL calculation when reducing / flipping position
        realized_pnl = pos.realized_pnl
        new_avg_entry = pos.avg_entry_price

        if pos.quantity != 0 and (pos.quantity > 0) != (delta_qty > 0):
            # Closing portion
            closing_qty = min(abs(pos.quantity), abs(delta_qty))
            pnl_direction = 1.0 if pos.quantity > 0 else -1.0
            pnl = closing_qty * (price - pos.avg_entry_price) * pnl_direction
            realized_pnl += pnl

            if abs(new_qty) < 1e-9:
                new_avg_entry = 0.0
                new_qty = 0.0
            elif (new_qty > 0) == (pos.quantity > 0):
                # Reduced but not flipped
                new_avg_entry = pos.avg_entry_price
            else:
                # Flipped
                new_avg_entry = price
        else:
            # Increasing position
            if abs(new_qty) > 1e-9:
                total_cost = (abs(pos.quantity) * pos.avg_entry_price) + (qty * price)
                new_avg_entry = total_cost / abs(new_qty)

        updated_pos = Position(
            symbol=symbol.upper(),
            quantity=new_qty,
            avg_entry_price=new_avg_entry,
            realized_pnl=realized_pnl,
            unrealized_pnl=0.0,
            updated_at=now,
        )
        self._positions[symbol.upper()] = updated_pos
        return updated_pos
