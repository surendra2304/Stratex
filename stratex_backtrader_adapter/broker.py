"""stratex_backtrader_adapter/broker.py

Simulated Broker inspired by Backtrader:
- Tracks cash, equity, open orders, and portfolio positions
- Applies configurable commissions (percentage, fixed fee) and slippage (basis points)
- Resolves fills and records closed round-trip TradeRecords
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from .models import (
    BacktestOrder,
    TradeRecord,
    OrderSide,
    OrderType,
    OrderStatus,
)


@dataclass
class CommissionScheme:
    """Configures broker fee and friction parameters."""
    commission_pct: float = 0.0005  # 5 bps
    fixed_fee: float = 0.0          # $0 fixed fee per order
    slippage_bps: float = 2.0       # 2 bps slippage


class BacktraderBroker:
    """Simulates realistic broker mechanics, account balance, and order execution."""

    def __init__(
        self,
        cash: float = 10000.0,
        commission_scheme: CommissionScheme | None = None,
    ) -> None:
        self.initial_cash = float(cash)
        self.cash = float(cash)
        self.commission_scheme = commission_scheme or CommissionScheme()
        self._positions: dict[str, float] = {}          # symbol -> size
        self._cost_basis: dict[str, float] = {}         # symbol -> avg entry price
        self._entry_indices: dict[str, int] = {}        # symbol -> bar index
        self._open_orders: list[BacktestOrder] = []
        self._completed_trades: list[TradeRecord] = []

    def get_cash(self) -> float:
        """Returns current available liquid cash."""
        return self.cash

    def get_position(self, symbol: str) -> float:
        """Returns net position quantity for symbol."""
        return self._positions.get(symbol.upper(), 0.0)

    def get_value(self, current_prices: dict[str, float]) -> float:
        """Calculates total portfolio value (cash + unrealized position market value)."""
        value = self.cash
        for sym, qty in self._positions.items():
            if qty != 0:
                price = current_prices.get(sym, self._cost_basis.get(sym, 0.0))
                value += qty * price
        return value

    def submit_order(self, order: BacktestOrder) -> BacktestOrder:
        """Submits an order to the broker queue."""
        order.status = OrderStatus.ACCEPTED
        self._open_orders.append(order)
        return order

    def cancel_order(self, order_id: str) -> bool:
        """Cancels an open order."""
        for o in self._open_orders:
            if o.order_id == order_id and not o.status.is_closed:
                o.status = OrderStatus.CANCELED
                self._open_orders.remove(o)
                return True
        return False

    def process_bar(
        self,
        bar_idx: int,
        market_data: dict[str, dict[str, float]],
    ) -> list[TradeRecord]:
        """Matches open orders against the current bar prices and returns newly closed trades."""
        closed_trades: list[TradeRecord] = []
        remaining_orders: list[BacktestOrder] = []

        for order in self._open_orders:
            sym = order.symbol.upper()
            if sym not in market_data:
                remaining_orders.append(order)
                continue

            bar = market_data[sym]
            open_p = bar.get("open", bar.get("close", 0.0))
            high_p = bar.get("high", open_p)
            low_p = bar.get("low", open_p)
            close_p = bar.get("close", open_p)

            can_fill = False
            exec_price = open_p

            if order.order_type == OrderType.MARKET:
                can_fill = True
                exec_price = open_p
            elif order.order_type == OrderType.LIMIT and order.price is not None:
                if order.side == OrderSide.BUY and low_p <= order.price:
                    can_fill = True
                    exec_price = min(open_p, order.price)
                elif order.side == OrderSide.SELL and high_p >= order.price:
                    can_fill = True
                    exec_price = max(open_p, order.price)
            elif order.order_type == OrderType.STOP and order.price is not None:
                if order.side == OrderSide.BUY and high_p >= order.price:
                    can_fill = True
                    exec_price = max(open_p, order.price)
                elif order.side == OrderSide.SELL and low_p <= order.price:
                    can_fill = True
                    exec_price = min(open_p, order.price)

            if can_fill:
                # Apply slippage
                slippage_factor = self.commission_scheme.slippage_bps / 10000.0
                if order.side == OrderSide.BUY:
                    final_price = exec_price * (1.0 + slippage_factor)
                else:
                    final_price = exec_price * (1.0 - slippage_factor)

                # Apply commission
                trade_notional = final_price * order.size
                comm = (trade_notional * self.commission_scheme.commission_pct) + self.commission_scheme.fixed_fee

                order.status = OrderStatus.COMPLETED
                order.executed_idx = bar_idx
                order.executed_price = final_price
                order.commission = comm

                # Update positions and calculate closed PnL if closing
                trade = self._apply_fill(order, final_price, comm, bar_idx)
                if trade:
                    closed_trades.append(trade)
                    self._completed_trades.append(trade)
            else:
                remaining_orders.append(order)

        self._open_orders = remaining_orders
        return closed_trades

    def _apply_fill(
        self,
        order: BacktestOrder,
        fill_price: float,
        commission: float,
        bar_idx: int,
    ) -> TradeRecord | None:
        """Updates internal cash and positions upon order execution."""
        sym = order.symbol.upper()
        current_pos = self._positions.get(sym, 0.0)
        current_entry = self._cost_basis.get(sym, 0.0)
        entry_idx = self._entry_indices.get(sym, bar_idx)

        trade_record: TradeRecord | None = None
        fill_qty = order.size if order.side == OrderSide.BUY else -order.size

        # Deduct cash for purchase or add cash for sale, minus commission
        if order.side == OrderSide.BUY:
            self.cash -= (fill_price * order.size) + commission
        else:
            self.cash += (fill_price * order.size) - commission

        # Check if closing part or all of existing position
        if current_pos != 0.0 and (current_pos > 0) != (fill_qty > 0):
            closed_size = min(abs(current_pos), abs(fill_qty))
            pnl_direction = 1.0 if current_pos > 0 else -1.0
            gross_pnl = closed_size * (fill_price - current_entry) * pnl_direction
            net_pnl = gross_pnl - commission
            cost_basis_notional = closed_size * current_entry
            pnl_pct = (net_pnl / cost_basis_notional * 100.0) if cost_basis_notional > 0 else 0.0

            trade_record = TradeRecord(
                trade_id=f"trd_{uuid.uuid4().hex[:8]}",
                symbol=sym,
                side=OrderSide.BUY if current_pos > 0 else OrderSide.SELL,
                size=closed_size,
                entry_price=current_entry,
                exit_price=fill_price,
                entry_idx=entry_idx,
                exit_idx=bar_idx,
                pnl=net_pnl,
                pnl_pct=pnl_pct,
                commission=commission,
                duration_bars=max(1, bar_idx - entry_idx),
            )

            new_pos = current_pos + fill_qty
            if abs(new_pos) < 1e-9:
                self._positions[sym] = 0.0
                self._cost_basis[sym] = 0.0
            elif (new_pos > 0) == (current_pos > 0):
                # Reduced but not flipped
                self._positions[sym] = new_pos
            else:
                # Flipped position
                self._positions[sym] = new_pos
                self._cost_basis[sym] = fill_price
                self._entry_indices[sym] = bar_idx
        else:
            # Increasing position or opening new position
            new_pos = current_pos + fill_qty
            if abs(new_pos) > 1e-9:
                total_cost = (abs(current_pos) * current_entry) + (order.size * fill_price)
                self._cost_basis[sym] = total_cost / abs(new_pos)
                self._positions[sym] = new_pos
                if current_pos == 0.0:
                    self._entry_indices[sym] = bar_idx

        return trade_record
