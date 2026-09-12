"""stratex_nautilus_adapter/orders.py

High-integrity Order State Machine and Contingency/Bracket Order Manager:
- Strict state transition validation (SUBMITTED -> ACCEPTED -> FILLED/CANCELED)
- OCO (One-Cancels-the-Other) linked order lifecycle
- OTO (One-Triggers-the-Other) linked order activation
- Bracket orders (Entry + Take-Profit Limit + Stop-Loss Stop) with automatic state orchestration
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from dataclasses import replace

from .models import (
    NautilusOrder,
    BracketOrder,
    OrderSide,
    OrderType,
    OrderState,
    TimeInForce,
    ContingencyType,
)


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
        """Instantiates and registers a new order in SUBMITTED state."""
        order_id = client_order_id or f"nt_ord_{uuid.uuid4().hex[:12]}"
        now = datetime.now(timezone.utc)
        order = NautilusOrder(
            client_order_id=order_id,
            symbol=symbol.upper(),
            side=side,
            order_type=order_type,
            quantity=float(quantity),
            price=float(price) if price is not None else None,
            stop_price=float(stop_price) if stop_price is not None else None,
            time_in_force=time_in_force,
            state=OrderState.SUBMITTED,
            filled_qty=0.0,
            remaining_qty=float(quantity),
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
        """Applies valid state transition and triggers OCO/OTO side effects."""
        current_order = self.get_order(client_order_id)
        if new_state != current_order.state:
            allowed = self._ALLOWED_TRANSITIONS.get(current_order.state, set())
            if new_state not in allowed:
                raise InvalidStateTransitionError(
                    f"Illegal state transition for order {client_order_id}: "
                    f"{current_order.state.value} -> {new_state.value}"
                )

        now = datetime.now(timezone.utc)
        updated_filled = current_order.filled_qty + fill_qty
        updated_remaining = max(0.0, current_order.quantity - updated_filled)

        # Update average fill price
        if updated_filled > 0 and fill_qty > 0 and fill_price > 0:
            total_cost = (current_order.filled_qty * current_order.avg_fill_price) + (fill_qty * fill_price)
            new_avg_price = total_cost / updated_filled
        else:
            new_avg_price = current_order.avg_fill_price

        updated_order = replace(
            current_order,
            state=new_state,
            filled_qty=updated_filled,
            remaining_qty=updated_remaining,
            avg_fill_price=new_avg_price,
            updated_at=now,
        )
        self._orders[client_order_id] = updated_order

        # Handle OCO logic: if order is FILLED, cancel linked partner
        if new_state == OrderState.FILLED and client_order_id in self._oco_links:
            partner_id = self._oco_links[client_order_id]
            partner_order = self._orders.get(partner_id)
            if partner_order and partner_order.state.is_open:
                self.cancel_order(partner_id)

        # Handle OTO logic: if order is FILLED, activate children to ACCEPTED
        if new_state == OrderState.FILLED and client_order_id in self._oto_triggers:
            children = self._oto_triggers[client_order_id]
            for child_id in children:
                child = self._orders.get(child_id)
                if child and child.state == OrderState.SUBMITTED:
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
        """Creates an institutional Bracket Order: Entry + Take Profit (Limit) + Stop Loss (Stop).
        
        The Take-Profit and Stop-Loss orders are formed as an OCO pair and linked as OTO children
        of the Entry order.
        """
        bracket_id = f"nt_brk_{uuid.uuid4().hex[:12]}"
        opp_side = OrderSide.SELL if side == OrderSide.BUY else OrderSide.BUY

        # 1. Entry order
        entry = self.create_order(
            symbol=symbol,
            side=side,
            order_type=entry_type,
            quantity=quantity,
            price=entry_price if entry_type == OrderType.LIMIT else None,
            contingency_type=ContingencyType.OTO,
        )

        # 2. Take-profit order (Limit)
        tp = self.create_order(
            symbol=symbol,
            side=opp_side,
            order_type=OrderType.LIMIT,
            quantity=quantity,
            price=take_profit_price,
            contingency_type=ContingencyType.OCO,
        )

        # 3. Stop-loss order (Stop Market)
        sl = self.create_order(
            symbol=symbol,
            side=opp_side,
            order_type=OrderType.STOP_MARKET,
            quantity=quantity,
            stop_price=stop_loss_price,
            contingency_type=ContingencyType.OCO,
        )

        # Link TP and SL as OCO
        self.link_oco(tp.client_order_id, sl.client_order_id)
        # Link Entry as OTO parent of TP and SL
        self.link_oto(entry.client_order_id, [tp.client_order_id, sl.client_order_id])

        bracket = BracketOrder(
            bracket_id=bracket_id,
            symbol=symbol.upper(),
            entry_order=entry,
            take_profit_order=tp,
            stop_loss_order=sl,
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
