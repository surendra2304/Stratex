"""stratex_nautilus_adapter/orders.py

High-integrity Order State Machine and Contingency/Bracket Order Manager:
- Strict state transition validation (SUBMITTED -> ACCEPTED -> FILLED/CANCELED)
- OCO (One-Cancels-the-Other) linked order lifecycle
- OTO (One-Triggers-the-Other) linked order activation
- Bracket orders (Entry + Take-Profit Limit + Stop-Loss Stop) with automatic state orchestration
"""

from __future__ import annotations

import math
import uuid
from datetime import datetime, timezone
from dataclasses import replace
from numbers import Real

from .models import (
    NautilusOrder,
    BracketOrder,
    OrderSide,
    OrderType,
    OrderState,
    TimeInForce,
    ContingencyType,
)



def _validated_float(value: object, field_name: str, *, allow_zero: bool = False) -> float:
    """Convert a finite real scalar after validating its risk-relevant bounds."""
    if isinstance(value, bool) or not isinstance(value, Real):
        raise ValueError(f"{field_name} must be a finite number")
    number = float(value)
    if not math.isfinite(number):
        raise ValueError(f"{field_name} must be a finite number")
    if number < 0.0 or (not allow_zero and number == 0.0):
        qualifier = "non-negative" if allow_zero else "positive"
        raise ValueError(f"{field_name} must be {qualifier}")
    return number


class InvalidStateTransitionError(ValueError):
    """Raised when an illegal order state transition is attempted."""
    pass


class OrderNotFoundError(KeyError):
    """Raised when an order ID is not tracked."""
    pass


class NautilusOrderManager:
    """Manages order state transitions, contingency linking (OCO/OTO), and bracket orders."""

    # Explicit allowed state transitions
    _ALLOWED_TRANSITIONS: dict[OrderState, set[OrderState]] = {
        OrderState.SUBMITTED: {OrderState.ACCEPTED, OrderState.REJECTED, OrderState.CANCELED},
        OrderState.ACCEPTED: {
            OrderState.PARTIALLY_FILLED,
            OrderState.FILLED,
            OrderState.PENDING_CANCEL,
            OrderState.CANCELED,
            OrderState.EXPIRED,
            OrderState.REJECTED,
        },
        OrderState.PARTIALLY_FILLED: {
            OrderState.PARTIALLY_FILLED,
            OrderState.FILLED,
            OrderState.PENDING_CANCEL,
            OrderState.CANCELED,
            OrderState.EXPIRED,
        },
        OrderState.PENDING_CANCEL: {OrderState.CANCELED, OrderState.FILLED},
        # Terminal states have no legal outgoing transitions
        OrderState.FILLED: set(),
        OrderState.CANCELED: set(),
        OrderState.REJECTED: set(),
        OrderState.EXPIRED: set(),
    }

    def __init__(self) -> None:
        self._orders: dict[str, NautilusOrder] = {}
        self._brackets: dict[str, BracketOrder] = {}
        # OCO pairings: order_id -> partner_order_id
        self._oco_links: dict[str, str] = {}
        # OTO triggers: parent_order_id -> list of child_order_ids
        self._oto_triggers: dict[str, list[str]] = {}

    def create_order(
        self,
        symbol: str,
        side: OrderSide,
        order_type: OrderType,
        quantity: float,
        price: float | None = None,
        stop_price: float | None = None,
        time_in_force: TimeInForce = TimeInForce.GTC,
        contingency_type: ContingencyType = ContingencyType.NONE,
        client_order_id: str | None = None,
    ) -> NautilusOrder:
        """Validate and register a unique order in SUBMITTED state."""
        if not isinstance(symbol, str) or not symbol.strip():
            raise ValueError("symbol must be a non-empty string")
        symbol = symbol.strip().upper()

        try:
            side = side if isinstance(side, OrderSide) else OrderSide(str(side).upper())
            order_type = (
                order_type if isinstance(order_type, OrderType) else OrderType(str(order_type).upper())
            )
            time_in_force = (
                time_in_force
                if isinstance(time_in_force, TimeInForce)
                else TimeInForce(str(time_in_force).upper())
            )
            contingency_type = (
                contingency_type
                if isinstance(contingency_type, ContingencyType)
                else ContingencyType(str(contingency_type).upper())
            )
        except (TypeError, ValueError) as exc:
            raise ValueError("side, order_type, time_in_force, or contingency_type is invalid") from exc

        quantity_value = _validated_float(quantity, "quantity")
        price_value = _validated_float(price, "price") if price is not None else None
        stop_value = _validated_float(stop_price, "stop_price") if stop_price is not None else None

        if order_type is OrderType.LIMIT and price_value is None:
            raise ValueError("LIMIT orders require a positive price")
        if order_type is OrderType.STOP_MARKET and stop_value is None:
            raise ValueError("STOP_MARKET orders require a positive stop_price")
        if order_type is OrderType.STOP_LIMIT and (price_value is None or stop_value is None):
            raise ValueError("STOP_LIMIT orders require positive price and stop_price values")
        if order_type is OrderType.TRAILING_STOP and stop_value is None:
            raise ValueError("TRAILING_STOP orders require a positive stop_price")

        if client_order_id is None:
            order_id = f"nt_ord_{uuid.uuid4().hex[:12]}"
        else:
            if not isinstance(client_order_id, str) or not client_order_id.strip():
                raise ValueError("client_order_id must be a non-empty string when supplied")
            order_id = client_order_id.strip()
        if order_id in self._orders:
            raise ValueError(f"client_order_id {order_id!r} already exists")

        now = datetime.now(timezone.utc)
        order = NautilusOrder(
            client_order_id=order_id,
            symbol=symbol,
            side=side,
            order_type=order_type,
            quantity=quantity_value,
            price=price_value,
            stop_price=stop_value,
            time_in_force=time_in_force,
            state=OrderState.SUBMITTED,
            filled_qty=0.0,
            remaining_qty=quantity_value,
            avg_fill_price=0.0,
            contingency_type=contingency_type,
            created_at=now,
            updated_at=now,
        )
        self._orders[order_id] = order
        return order

    def get_order(self, client_order_id: str) -> NautilusOrder:
        """Retrieves order by client order ID."""
        if client_order_id not in self._orders:
            raise OrderNotFoundError(f"Order {client_order_id} not found.")
        return self._orders[client_order_id]

    def list_orders(
        self,
        symbol: str | None = None,
        is_open: bool | None = None,
    ) -> list[NautilusOrder]:
        """Lists tracked orders with optional symbol and open status filtering."""
        orders = list(self._orders.values())
        if symbol:
            sym_upper = symbol.upper()
            orders = [o for o in orders if o.symbol == sym_upper]
        if is_open is not None:
            orders = [o for o in orders if o.state.is_open == is_open]
        return orders

    def update_order_state(
        self,
        client_order_id: str,
        new_state: OrderState,
        fill_qty: float = 0.0,
        fill_price: float = 0.0,
    ) -> NautilusOrder:
        """Apply a legal state transition without allowing invalid or excessive fills."""
        current_order = self.get_order(client_order_id)
        try:
            new_state = new_state if isinstance(new_state, OrderState) else OrderState(str(new_state).upper())
        except (TypeError, ValueError) as exc:
            raise ValueError("new_state must be a valid OrderState") from exc

        fill_quantity = _validated_float(fill_qty, "fill_qty", allow_zero=True)
        execution_price = _validated_float(fill_price, "fill_price", allow_zero=True)
        if fill_quantity > 0.0 and execution_price <= 0.0:
            raise ValueError("fill_price must be positive when fill_qty is positive")
        if fill_quantity > 0.0 and new_state not in (OrderState.PARTIALLY_FILLED, OrderState.FILLED):
            raise ValueError("fill_qty is only valid for PARTIALLY_FILLED or FILLED transitions")

        # Exact duplicate state notifications are idempotent; they must not
        # update timestamps or mutate quantities a second time.
        if new_state is current_order.state and fill_quantity == 0.0:
            return current_order

        if new_state != current_order.state:
            allowed = self._ALLOWED_TRANSITIONS.get(current_order.state, set())
            if new_state not in allowed:
                raise InvalidStateTransitionError(
                    f"Illegal state transition for order {client_order_id}: "
                    f"{current_order.state.value} -> {new_state.value}"
                )

        remaining = float(current_order.remaining_qty)
        if fill_quantity > remaining:
            raise ValueError(f"fill_qty {fill_quantity} exceeds remaining quantity {remaining}")

        if new_state is OrderState.PARTIALLY_FILLED:
            if fill_quantity <= 0.0:
                raise ValueError("PARTIALLY_FILLED transitions require a positive fill_qty")
            if fill_quantity >= remaining:
                raise ValueError("PARTIALLY_FILLED cannot consume the entire remaining quantity")
        elif new_state is OrderState.FILLED:
            if not math.isclose(fill_quantity, remaining, rel_tol=1e-9, abs_tol=1e-12):
                raise ValueError("FILLED transitions must fill the entire remaining quantity")
        elif fill_quantity > 0.0:
            raise ValueError("non-fill state transitions cannot contain fill quantity")

        updated_filled = float(current_order.filled_qty) + fill_quantity
        if not math.isfinite(updated_filled):
            raise ValueError("cumulative filled quantity must remain finite")
        if new_state is OrderState.FILLED:
            # Normalize tiny floating-point residue after the full-fill tolerance.
            updated_filled = float(current_order.quantity)
            updated_remaining = 0.0
        else:
            updated_remaining = float(current_order.quantity) - updated_filled
            if updated_remaining < 0.0 or not math.isfinite(updated_remaining):
                raise ValueError("remaining quantity must be finite and non-negative")

        new_avg_price = float(current_order.avg_fill_price)
        if fill_quantity > 0.0:
            if updated_filled <= 0.0:
                raise ValueError("cumulative filled quantity must be positive")
            # Weighted terms avoid an intermediate quantity*price overflow.
            new_avg_price = (
                (float(current_order.avg_fill_price) * (float(current_order.filled_qty) / updated_filled))
                + (execution_price * (fill_quantity / updated_filled))
            )
            if not math.isfinite(new_avg_price) or new_avg_price <= 0.0:
                raise ValueError("average fill price must remain finite and positive")

        updated_order = replace(
            current_order,
            state=new_state,
            filled_qty=updated_filled,
            remaining_qty=updated_remaining,
            avg_fill_price=new_avg_price,
            updated_at=datetime.now(timezone.utc),
        )
        self._orders[client_order_id] = updated_order

        # Preserve contingency semantics but activate/cancel only after the
        # parent order's validated full fill is committed.
        if new_state is OrderState.FILLED and client_order_id in self._oco_links:
            partner_id = self._oco_links[client_order_id]
            partner_order = self._orders.get(partner_id)
            if partner_order and partner_order.state.is_open:
                self.cancel_order(partner_id)

        if new_state is OrderState.FILLED and client_order_id in self._oto_triggers:
            for child_id in self._oto_triggers[client_order_id]:
                child = self._orders.get(child_id)
                if child and child.state is OrderState.SUBMITTED:
                    self.update_order_state(child_id, OrderState.ACCEPTED)

        return updated_order

    def cancel_order(self, client_order_id: str) -> NautilusOrder:
        """Cancels an open order."""
        return self.update_order_state(client_order_id, OrderState.CANCELED)

    def link_oco(self, order_a_id: str, order_b_id: str) -> None:
        """Links two orders as an OCO (One-Cancels-the-Other) pair."""
        if order_a_id not in self._orders or order_b_id not in self._orders:
            raise OrderNotFoundError("Both orders must exist before linking OCO.")
        self._oco_links[order_a_id] = order_b_id
        self._oco_links[order_b_id] = order_a_id

    def link_oto(self, parent_order_id: str, child_order_ids: list[str]) -> None:
        """Links child orders to a parent order (One-Triggers-the-Other)."""
        if parent_order_id not in self._orders:
            raise OrderNotFoundError("Parent order must exist before linking OTO.")
        for cid in child_order_ids:
            if cid not in self._orders:
                raise OrderNotFoundError(f"Child order {cid} must exist before linking OTO.")
        self._oto_triggers[parent_order_id] = list(child_order_ids)

    def create_bracket_order(
        self,
        symbol: str,
        side: OrderSide,
        quantity: float,
        entry_price: float,
        take_profit_price: float,
        stop_loss_price: float,
        entry_type: OrderType = OrderType.LIMIT,
    ) -> BracketOrder:
        """Create a validated entry with OTO-activated, OCO-linked exits."""
        if not isinstance(symbol, str) or not symbol.strip():
            raise ValueError("symbol must be a non-empty string")
        symbol = symbol.strip().upper()
        try:
            side = side if isinstance(side, OrderSide) else OrderSide(str(side).upper())
            entry_type = (
                entry_type if isinstance(entry_type, OrderType) else OrderType(str(entry_type).upper())
            )
        except (TypeError, ValueError) as exc:
            raise ValueError("side or entry_type is invalid") from exc
        if entry_type not in (OrderType.LIMIT, OrderType.MARKET):
            raise ValueError("bracket entry_type must be LIMIT or MARKET")

        quantity_value = _validated_float(quantity, "quantity")
        entry_value = _validated_float(entry_price, "entry_price")
        take_profit_value = _validated_float(take_profit_price, "take_profit_price")
        stop_loss_value = _validated_float(stop_loss_price, "stop_loss_price")

        if side is OrderSide.BUY:
            levels_valid = stop_loss_value < entry_value < take_profit_value
        else:
            levels_valid = take_profit_value < entry_value < stop_loss_value
        if not levels_valid:
            raise ValueError(
                "bracket prices are inconsistent with the side: expected stop-loss < entry < take-profit "
                "for BUY, and take-profit < entry < stop-loss for SELL"
            )

        bracket_id = f"nt_brk_{uuid.uuid4().hex[:12]}"
        opposite_side = OrderSide.SELL if side is OrderSide.BUY else OrderSide.BUY

        entry = self.create_order(
            symbol=symbol,
            side=side,
            order_type=entry_type,
            quantity=quantity_value,
            price=entry_value if entry_type is OrderType.LIMIT else None,
            contingency_type=ContingencyType.OTO,
        )
        take_profit = self.create_order(
            symbol=symbol,
            side=opposite_side,
            order_type=OrderType.LIMIT,
            quantity=quantity_value,
            price=take_profit_value,
            contingency_type=ContingencyType.OCO,
        )
        stop_loss = self.create_order(
            symbol=symbol,
            side=opposite_side,
            order_type=OrderType.STOP_MARKET,
            quantity=quantity_value,
            stop_price=stop_loss_value,
            contingency_type=ContingencyType.OCO,
        )

        self.link_oco(take_profit.client_order_id, stop_loss.client_order_id)
        self.link_oto(entry.client_order_id, [take_profit.client_order_id, stop_loss.client_order_id])

        bracket = BracketOrder(
            bracket_id=bracket_id,
            symbol=symbol,
            entry_order=entry,
            take_profit_order=take_profit,
            stop_loss_order=stop_loss,
            is_active=True,
        )
        self._brackets[bracket_id] = bracket
        return bracket

    def get_bracket(self, bracket_id: str) -> BracketOrder:
        """Retrieves bracket order by ID."""
        if bracket_id not in self._brackets:
            raise KeyError(f"Bracket order {bracket_id} not found.")
        return self._brackets[bracket_id]

    def list_brackets(self) -> list[BracketOrder]:
        """Lists all bracket orders."""
        return list(self._brackets.values())


# Default global order manager singleton
default_order_manager = NautilusOrderManager()
