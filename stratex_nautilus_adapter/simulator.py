"""Deterministic event-driven execution simulation with validated market inputs.

The simulator is deliberately local-only: it never routes live orders. It
validates every trade event, models slippage/fees, prevents invalid arithmetic
from mutating order or position state, and deduplicates repeated trade IDs.
"""

from __future__ import annotations

import math
import uuid
from collections import OrderedDict
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from numbers import Integral, Real

from .models import (
    FillReport,
    LiquidityRole,
    NautilusOrder,
    OrderSide,
    OrderState,
    OrderType,
    Position,
    TimeInForce,
    TradeTick,
)
from .orders import NautilusOrderManager


class SlippageModel(str, Enum):
    ZERO = "ZERO"
    FIXED_BPS = "FIXED_BPS"
    MARKET_IMPACT = "MARKET_IMPACT"


def _finite_real(value: object, field_name: str, *, positive: bool = False, non_negative: bool = False) -> float:
    """Validate and normalize a finite real scalar, rejecting bool coercions."""
    if isinstance(value, bool) or not isinstance(value, Real):
        raise ValueError(f"{field_name} must be a finite real number")
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError(f"{field_name} must be a finite real number") from exc
    if not math.isfinite(number):
        raise ValueError(f"{field_name} must be a finite real number")
    if positive and number <= 0.0:
        raise ValueError(f"{field_name} must be positive")
    if non_negative and number < 0.0:
        raise ValueError(f"{field_name} must be non-negative")
    return number


@dataclass
class SimulatorConfig:
    """Execution-friction parameters expressed as non-negative fractions/units."""

    slippage_model: SlippageModel = SlippageModel.FIXED_BPS
    fixed_slippage_bps: float = 2.0
    impact_constant: float = 0.05
    taker_fee_pct: float = 0.0005
    maker_fee_pct: float = 0.0002
    latency_ms: float = 5.0

    def __post_init__(self) -> None:
        self.validate()

    def validate(self) -> None:
        """Revalidate at use time too, because this config remains mutable."""
        try:
            if not isinstance(self.slippage_model, SlippageModel):
                self.slippage_model = SlippageModel(str(self.slippage_model).upper())
        except (TypeError, ValueError) as exc:
            raise ValueError("slippage_model must be ZERO, FIXED_BPS, or MARKET_IMPACT") from exc

        self.fixed_slippage_bps = _finite_real(
            self.fixed_slippage_bps, "fixed_slippage_bps", non_negative=True
        )
        self.impact_constant = _finite_real(self.impact_constant, "impact_constant", non_negative=True)
        self.latency_ms = _finite_real(self.latency_ms, "latency_ms", non_negative=True)

        for field_name in ("taker_fee_pct", "maker_fee_pct"):
            fee = _finite_real(getattr(self, field_name), field_name, non_negative=True)
            if fee >= 1.0:
                raise ValueError(f"{field_name} must be less than 1.0 (100 percent)")

        if self.fixed_slippage_bps >= 10000.0:
            raise ValueError("fixed_slippage_bps must be less than 10000 to keep sell fills positive")
        worst_impact = self.fixed_slippage_bps / 10000.0 + self.impact_constant * 0.01
        if not math.isfinite(worst_impact) or worst_impact >= 1.0:
            raise ValueError("configured slippage must remain below 100 percent")


class NautilusExecutionSimulator:
    """Simulate local fills while preserving finite, internally consistent state."""

    # Bound duplicate detection so long-running streams cannot grow memory forever.
    MAX_REMEMBERED_TRADE_IDS = 100_000

    def __init__(
        self,
        config: SimulatorConfig | None = None,
        order_manager: NautilusOrderManager | None = None,
    ) -> None:
        self.config = config if config is not None else SimulatorConfig()
        if not isinstance(self.config, SimulatorConfig):
            raise TypeError("config must be a SimulatorConfig or None")
        self.config.validate()

        self.order_manager = order_manager if order_manager is not None else NautilusOrderManager()
        if not isinstance(self.order_manager, NautilusOrderManager):
            raise TypeError("order_manager must be a NautilusOrderManager or None")

        self._positions: dict[str, Position] = {}
        self._fills: list[FillReport] = []
        self._last_tick_ts_by_symbol: dict[str, int] = {}
        self._seen_trade_ids: OrderedDict[tuple[str, str], None] = OrderedDict()

    @staticmethod
    def _normalize_symbol(symbol: object, field_name: str = "symbol") -> str:
        if not isinstance(symbol, str) or not symbol.strip():
            raise ValueError(f"{field_name} must be a non-empty string")
        return symbol.strip().upper()

    @classmethod
    def _validate_tick(cls, tick: TradeTick) -> tuple[str, int, str]:
        """Validate market data before it is used to match or mutate anything."""
        if not isinstance(tick, TradeTick):
            raise TypeError("tick must be a TradeTick")
        symbol = cls._normalize_symbol(tick.symbol, "tick symbol")
        _finite_real(tick.price, "tick price", positive=True)
        _finite_real(tick.size, "tick size", positive=True)
        if isinstance(tick.ts_event, bool) or not isinstance(tick.ts_event, Integral) or tick.ts_event < 0:
            raise ValueError("tick ts_event must be a non-negative integer nanosecond timestamp")
        if not isinstance(tick.side, OrderSide):
            raise ValueError("tick side must be BUY or SELL")
        if not isinstance(tick.trade_id, str) or not tick.trade_id.strip():
            raise ValueError("tick trade_id must be a non-empty string")
        return symbol, int(tick.ts_event), tick.trade_id.strip()

    def get_position(self, symbol: str) -> Position:
        """Return the latest signed position, or a new flat snapshot for this symbol."""
        sym_upper = self._normalize_symbol(symbol)
        if sym_upper not in self._positions:
            self._positions[sym_upper] = Position(symbol=sym_upper)
        return self._positions[sym_upper]

    def list_fills(self, symbol: str | None = None) -> list[FillReport]:
        """Return a defensive list copy, optionally filtered by symbol."""
        if symbol is None:
            return list(self._fills)
        sym_upper = self._normalize_symbol(symbol)
        return [fill for fill in self._fills if fill.symbol == sym_upper]

    def calculate_slippage(
        self,
        base_price: float,
        side: OrderSide,
        quantity: float,
        liquidity_volume: float = 100.0,
    ) -> float:
        """Calculate a finite, positive execution price after bounded slippage."""
        self.config.validate()
        base = _finite_real(base_price, "base_price", positive=True)
        qty = _finite_real(quantity, "quantity", positive=True)
        liquidity = _finite_real(liquidity_volume, "liquidity_volume", non_negative=True)
        if not isinstance(side, OrderSide):
            raise ValueError("side must be BUY or SELL")

        if self.config.slippage_model is SlippageModel.ZERO:
            return base

        sign = 1.0 if side is OrderSide.BUY else -1.0
        slippage_fraction = self.config.fixed_slippage_bps / 10000.0
        if self.config.slippage_model is SlippageModel.MARKET_IMPACT:
            volume_ratio = qty / max(liquidity, qty)
            slippage_fraction += self.config.impact_constant * math.sqrt(volume_ratio) * 0.01

        fill_price = base * (1.0 + sign * slippage_fraction)
        if not math.isfinite(fill_price) or fill_price <= 0.0:
            raise ValueError("calculated fill price must be finite and positive")
        return fill_price

    @staticmethod
    def _can_fill(order: NautilusOrder, tick: TradeTick) -> tuple[bool, bool, float]:
        """Return (fillable, maker, reference price) without changing order state."""
        if not isinstance(order.side, OrderSide):
            raise ValueError("order side must be BUY or SELL")
        if not math.isfinite(float(order.remaining_qty)) or order.remaining_qty <= 0.0:
            raise ValueError("open order remaining quantity must be finite and positive")

        if order.order_type is OrderType.MARKET:
            return True, False, tick.price

        if order.order_type is OrderType.LIMIT:
            if order.price is None:
                raise ValueError("LIMIT order is missing a price")
            limit_price = _finite_real(order.price, "limit price", positive=True)
            is_marketable = (
                tick.price <= limit_price if order.side is OrderSide.BUY else tick.price >= limit_price
            )
            # Price improvement is credited to the trader instead of forcing
            # execution at a worse limit price when the observed trade is better.
            return is_marketable, True, tick.price

        if order.order_type is OrderType.STOP_MARKET:
            if order.stop_price is None:
                raise ValueError("STOP_MARKET order is missing a stop price")
            stop_price = _finite_real(order.stop_price, "stop price", positive=True)
            triggered = (
                tick.price >= stop_price if order.side is OrderSide.BUY else tick.price <= stop_price
            )
            return triggered, False, tick.price

        if order.order_type is OrderType.STOP_LIMIT:
            raise ValueError("STOP_LIMIT orders are unsupported until trigger persistence is implemented")
        if order.order_type is OrderType.TRAILING_STOP:
            raise ValueError("TRAILING_STOP orders are unsupported without a trailing-offset model")
        raise ValueError("order_type is invalid")

    @staticmethod
    def _calculate_updated_position(
        symbol: str,
        side: OrderSide,
        qty: float,
        price: float,
        current: Position,
    ) -> Position:
        """Compute a complete next position snapshot without mutating simulator state."""
        sym_upper = NautilusExecutionSimulator._normalize_symbol(symbol)
        quantity = _finite_real(qty, "fill quantity", positive=True)
        fill_price = _finite_real(price, "fill price", positive=True)
        if not isinstance(side, OrderSide):
            raise ValueError("side must be BUY or SELL")
        if not isinstance(current, Position):
            raise ValueError("current position must be a Position")
        if current.symbol.strip().upper() != sym_upper:
            raise ValueError("current position symbol does not match fill symbol")

        previous_qty = _finite_real(current.quantity, "position quantity")
        previous_avg = _finite_real(current.avg_entry_price, "position average entry price", non_negative=True)
        previous_realized = _finite_real(current.realized_pnl, "realized PnL")
        if previous_qty != 0.0 and previous_avg <= 0.0:
            raise ValueError("non-flat position must have a positive average entry price")

        delta_qty = quantity if side is OrderSide.BUY else -quantity
        new_qty = previous_qty + delta_qty
        if not math.isfinite(new_qty):
            raise ValueError("updated position quantity overflowed")

        realized_pnl = previous_realized
        new_avg_entry = previous_avg
        opposite_side = previous_qty != 0.0 and ((previous_qty > 0.0) != (delta_qty > 0.0))

        if opposite_side:
            closing_qty = min(abs(previous_qty), abs(delta_qty))
            pnl_per_unit = fill_price - previous_avg
            if not math.isfinite(pnl_per_unit):
                raise ValueError("realized PnL price difference overflowed")
            pnl_direction = 1.0 if previous_qty > 0.0 else -1.0
            pnl = closing_qty * pnl_per_unit * pnl_direction
            if not math.isfinite(pnl):
                raise ValueError("realized PnL overflowed")
            realized_pnl += pnl
            if not math.isfinite(realized_pnl):
                raise ValueError("cumulative realized PnL overflowed")

            if new_qty == 0.0:
                new_avg_entry = 0.0
            elif (new_qty > 0.0) == (previous_qty > 0.0):
                new_avg_entry = previous_avg
            else:
                new_avg_entry = fill_price
        elif new_qty != 0.0:
            total_abs_qty = abs(new_qty)
            # Weighted terms avoid overflowing q * price when the result is a
            # well-defined weighted mean within the two finite input prices.
            previous_weight = abs(previous_qty) / total_abs_qty
            fill_weight = quantity / total_abs_qty
            new_avg_entry = previous_weight * previous_avg + fill_weight * fill_price

        if not math.isfinite(new_avg_entry) or new_avg_entry < 0.0:
            raise ValueError("updated average entry price is invalid")
        return Position(
            symbol=sym_upper,
            quantity=new_qty,
            avg_entry_price=new_avg_entry,
            realized_pnl=realized_pnl,
            unrealized_pnl=0.0,
            updated_at=datetime.now(timezone.utc),
        )

    def process_tick(self, tick: TradeTick) -> list[FillReport]:
        """Process one unique, chronologically ordered tick against open orders.

        A repeated trade ID is ignored. Invalid data is rejected before accepting
        orders or changing fill/position state. Each fill's next position is
        computed before the order lifecycle is mutated.
        """
        symbol, event_ts, trade_id = self._validate_tick(tick)
        key = (symbol, trade_id)
        if key in self._seen_trade_ids:
            return []

        last_timestamp = self._last_tick_ts_by_symbol.get(symbol)
        if last_timestamp is not None and event_ts < last_timestamp:
            raise ValueError("tick timestamps must be non-decreasing for each symbol")

        # Treat this tick as consumed before mutating order state. If an order
        # invariant fails below, replaying the same market event cannot double-fill
        # an order that was already processed before the failure.
        self._seen_trade_ids[key] = None
        if len(self._seen_trade_ids) > self.MAX_REMEMBERED_TRADE_IDS:
            self._seen_trade_ids.popitem(last=False)
        self._last_tick_ts_by_symbol[symbol] = event_ts

        open_orders = self.order_manager.list_orders(symbol=symbol, is_open=True)
        new_fills: list[FillReport] = []

        for snapshot in open_orders:
            # OCO/OTO updates from a prior order in this same loop can close a
            # sibling, so always re-fetch the latest snapshot before matching.
            order = self.order_manager.get_order(snapshot.client_order_id)
            if not order.state.is_open:
                continue

            # OTO exits must not match merely because they were registered.
            # They become eligible only after the parent entry is fully filled.
            if not self.order_manager.is_order_ready_for_matching(order.client_order_id):
                continue

            can_fill, is_maker, reference_price = self._can_fill(order, tick)
            if not can_fill:
                if order.state is OrderState.SUBMITTED:
                    order = self.order_manager.update_order_state(order.client_order_id, OrderState.ACCEPTED)
                if order.time_in_force in (TimeInForce.IOC, TimeInForce.FOK):
                    self.order_manager.cancel_order(order.client_order_id)
                continue

            if order.remaining_qty <= 0.0 or not math.isfinite(float(order.remaining_qty)):
                raise ValueError("fillable order must have a finite positive remaining quantity")
            quantity = float(order.remaining_qty)
            fill_price = (
                self.calculate_slippage(reference_price, order.side, quantity, tick.size)
                if order.order_type in (OrderType.MARKET, OrderType.STOP_MARKET)
                else _finite_real(reference_price, "fill price", positive=True)
            )
            if not math.isfinite(fill_price) or fill_price <= 0.0:
                raise ValueError("fill price must be finite and positive")

            self.config.validate()
            fee_rate = self.config.maker_fee_pct if is_maker else self.config.taker_fee_pct
            gross_notional = fill_price * quantity
            commission = gross_notional * fee_rate
            if not math.isfinite(gross_notional) or gross_notional <= 0.0:
                raise ValueError("fill notional must be finite and positive")
            if not math.isfinite(commission) or commission < 0.0:
                raise ValueError("fill commission must be finite and non-negative")

            old_position = self._positions.get(order.symbol.upper(), Position(symbol=order.symbol.upper()))
            updated_position = self._calculate_updated_position(
                order.symbol, order.side, quantity, fill_price, old_position
            )
            fill = FillReport(
                fill_id=f"fill_{uuid.uuid4().hex[:10]}",
                client_order_id=order.client_order_id,
                symbol=order.symbol,
                price=fill_price,
                quantity=quantity,
                commission=commission,
                liquidity_role=LiquidityRole.MAKER if is_maker else LiquidityRole.TAKER,
                timestamp=datetime.now(timezone.utc),
            )

            if order.state is OrderState.SUBMITTED:
                self.order_manager.update_order_state(order.client_order_id, OrderState.ACCEPTED)
            self.order_manager.update_order_state(
                order.client_order_id,
                new_state=OrderState.FILLED,
                fill_qty=quantity,
                fill_price=fill_price,
            )
            self._positions[order.symbol.upper()] = updated_position
            self._fills.append(fill)
            new_fills.append(fill)

        return new_fills

    def _update_position(
        self,
        symbol: str,
        side: OrderSide,
        qty: float,
        price: float,
    ) -> Position:
        """Update a position only after the complete replacement snapshot validates."""
        sym_upper = self._normalize_symbol(symbol)
        current = self._positions.get(sym_upper, Position(symbol=sym_upper))
        updated = self._calculate_updated_position(sym_upper, side, qty, price, current)
        self._positions[sym_upper] = updated
        return updated
