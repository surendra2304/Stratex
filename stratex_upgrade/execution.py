from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from decimal import Decimal
from typing import ClassVar, Protocol

from .models import Fill, OrderIntent, OrderRecord, OrderStatus, Side


class ExchangeAdapter(Protocol):
    def submit_order(self, intent: OrderIntent) -> str: ...
    def cancel_order(self, symbol: str, order_id: str) -> None: ...
    def fetch_order(self, symbol: str, order_id: str) -> dict: ...
    def fetch_open_orders(self, symbol: str | None = None) -> list[dict]: ...
    def fetch_positions(self) -> list[dict]: ...
    def fetch_account(self) -> dict: ...
    def fetch_market(self, symbol: str) -> dict: ...


@dataclass(slots=True)
class ExecutionConfig:
    submit_timeout_seconds: float = 10.0
    unknown_order_recheck_seconds: float = 2.0
    max_rechecks: int = 4
    require_client_order_id: bool = True


class ExecutionEngine:
    """Order state machine: intent -> submit -> reconcile -> fill -> final."""

    _TERMINAL: ClassVar[set[OrderStatus]] = {
        OrderStatus.FILLED,
        OrderStatus.CANCELLED,
        OrderStatus.REJECTED,
        OrderStatus.EXPIRED,
    }


    def __init__(self, adapter: ExchangeAdapter, config: ExecutionConfig | None = None):
        self.adapter = adapter
        self.config = config or ExecutionConfig()
        self._orders: dict[str, OrderRecord] = {}
        self._client_ids: dict[str, str] = {}
        self._lock = threading.RLock()

    def tracked_orders(self) -> list[OrderRecord]:
        with self._lock:
            return list(self._orders.values())

    def submit(self, intent: OrderIntent) -> OrderRecord:
        with self._lock:
            if self.config.require_client_order_id and not intent.client_order_id:
                raise ValueError("client_order_id required")
            existing_id = self._client_ids.get(intent.client_order_id)
            if existing_id:
                return self._orders[existing_id]
            order = OrderRecord(
                order_id="pending:" + intent.intent_id,
                intent_id=intent.intent_id,
                client_order_id=intent.client_order_id,
                symbol=intent.symbol,
                side=intent.side,
                quantity=intent.quantity,
                status=OrderStatus.PENDING_SUBMIT,
            )
            self._orders[order.order_id] = order
            self._client_ids[intent.client_order_id] = order.order_id

        try:
            exchange_id = self.adapter.submit_order(intent)
        except Exception as exc:  # noqa: BLE001

            with self._lock:
                order.status = OrderStatus.UNKNOWN
                order.reason = f"SUBMIT_EXCEPTION:{type(exc).__name__}"
                order.updated_at_ns = time.time_ns()
            self.reconcile(order)
            return order

        with self._lock:
            if order.order_id in self._orders:
                del self._orders[order.order_id]
            order.order_id = str(exchange_id)
            order.status = OrderStatus.SUBMITTED
            order.updated_at_ns = time.time_ns()
            self._orders[order.order_id] = order
            self._client_ids[intent.client_order_id] = order.order_id
        self.reconcile(order)
        return order

    def apply_fill(self, fill: Fill) -> OrderRecord:
        with self._lock:
            order = self._orders.get(fill.order_id)
            if order is None:
                raise KeyError(f"unknown order {fill.order_id}")
            order.apply_fill(fill)
            return order

    def reconcile(self, order: OrderRecord) -> OrderRecord:
        for _ in range(self.config.max_rechecks):
            snapshot = self.adapter.fetch_order(order.symbol, order.order_id)
            status = str(snapshot.get("status", "UNKNOWN")).upper()
            filled = Decimal(str(snapshot.get("executedQty", "0")))
            avg_price = snapshot.get("avgPrice") or snapshot.get("price")
            with self._lock:
                order.provider_status = status
                if status in ("FILLED", "PARTIALLY_FILLED", "CANCELED", "CANCELLED", "EXPIRED", "REJECTED"):
                    order.filled_quantity = max(order.filled_quantity, filled)
                    if avg_price is not None and Decimal(str(avg_price)) > 0:
                        order.avg_fill_price = Decimal(str(avg_price))
                    order.status = {
                        "FILLED": OrderStatus.FILLED,
                        "PARTIALLY_FILLED": OrderStatus.PARTIALLY_FILLED,
                        "CANCELED": OrderStatus.CANCELLED,
                        "CANCELLED": OrderStatus.CANCELLED,
                        "EXPIRED": OrderStatus.EXPIRED,
                        "REJECTED": OrderStatus.REJECTED,
                    }[status]
                    order.updated_at_ns = time.time_ns()
                    return order
                order.status = OrderStatus.UNKNOWN
            time.sleep(self.config.unknown_order_recheck_seconds)
        return order

    def cancel(self, symbol: str, order_id: str) -> OrderRecord:
        with self._lock:
            order = self._orders[order_id]
            if order.status in self._TERMINAL:
                return order
            order.status = OrderStatus.CANCEL_PENDING
        self.adapter.cancel_order(symbol, order_id)
        return self.reconcile(order)


class PaperExecutionAdapter:
    """Deterministic simulator implementing the same adapter contract."""

    def __init__(self, slippage_bps: Decimal = Decimal(5), fee_bps: Decimal = Decimal(10)):
        self.slippage_bps = slippage_bps
        self.fee_bps = fee_bps
        self._orders: dict[str, dict] = {}
        self._counter = 0
        self._lock = threading.Lock()

    def submit_order(self, intent: OrderIntent) -> str:
        with self._lock:
            self._counter += 1
            oid = f"paper-{self._counter}"
            self._orders[oid] = {
                "status": "NEW",
                "symbol": intent.symbol,
                "side": intent.side.value,
                "origQty": str(intent.quantity),
                "executedQty": "0",
                "price": str(intent.limit_price or "0"),
                "clientOrderId": intent.client_order_id,
            }
            return oid

    def fill_at(self, order_id: str, market_price: Decimal) -> Fill:
        with self._lock:
            order = self._orders[order_id]
            qty = Decimal(order["origQty"])
            direction = Decimal(1) if order["side"] == "BUY" else Decimal(-1)
            impact = self.slippage_bps / Decimal(10000)
            fill_price = market_price * (Decimal(1) + direction * impact)
            fee = fill_price * qty * self.fee_bps / Decimal(10000)
            order["status"] = "FILLED"
            order["executedQty"] = str(qty)
            order["price"] = str(fill_price)
            return Fill(
                fill_id=f"{order_id}-fill-1",
                order_id=order_id,
                symbol=order["symbol"],
                side=Side(order["side"]),
                quantity=qty,
                price=fill_price,
                fee=fee,
                fee_asset="USDT",
                timestamp_ns=time.time_ns(),
                liquidity="taker",
            )

    def cancel_order(self, symbol: str, order_id: str) -> None:
        with self._lock:
            if order_id in self._orders and self._orders[order_id]["status"] not in ("FILLED", "CANCELED"):
                self._orders[order_id]["status"] = "CANCELED"

    def fetch_order(self, symbol: str, order_id: str) -> dict:
        with self._lock:
            return dict(self._orders[order_id])

    def fetch_open_orders(self, symbol: str | None = None) -> list[dict]:
        with self._lock:
            return [
                dict(o, orderId=oid)
                for oid, o in self._orders.items()
                if o["status"] in ("NEW", "PARTIALLY_FILLED") and (symbol is None or o["symbol"] == symbol)
            ]

    def fetch_positions(self) -> list[dict]:
        return []

    def fetch_account(self) -> dict:
        return {"equity": "10000", "available": "10000"}

    def fetch_market(self, symbol: str) -> dict:
        return {"symbol": symbol, "price": "0"}
