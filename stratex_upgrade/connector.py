from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Protocol


@dataclass(frozen=True, slots=True)
class ExchangeCapabilities:
    spot: bool
    margin: bool
    futures: bool
    stop_orders: bool
    oco: bool
    websocket: bool


class ConnectorProtocol(Protocol):
    exchange_id: str
    capabilities: ExchangeCapabilities
    def market(self, symbol: str) -> dict[str, Any]: ...
    def create_order(self, symbol: str, side: str, order_type: str, amount: Decimal, price: Decimal | None = None, params: dict | None = None) -> dict[str, Any]: ...
    def fetch_order(self, symbol: str, order_id: str) -> dict[str, Any]: ...
    def cancel_order(self, symbol: str, order_id: str) -> dict[str, Any]: ...


class TokenBucket:
    def __init__(self, rate_per_second: float, capacity: float):
        self.rate = max(0.0001, rate_per_second)
        self.capacity = max(1.0, capacity)
        self.tokens = self.capacity
        self.updated = time.monotonic()
        self._lock = threading.Lock()

    def acquire(self, cost: float = 1.0) -> None:
        if cost <= 0:
            return
        while True:
            with self._lock:
                now = time.monotonic()
                self.tokens = min(self.capacity, self.tokens + (now - self.updated) * self.rate)
                self.updated = now
                if self.tokens >= cost:
                    self.tokens -= cost
                    return
                wait = (cost - self.tokens) / self.rate
            time.sleep(wait)


class CCXTStyleAdapter:
    """Thin normalized boundary; the actual ccxt exchange object can be injected."""

    def __init__(self, exchange: Any, exchange_id: str, rate_per_second: float = 5.0):
        self.exchange = exchange
        self.exchange_id = exchange_id
        self.capabilities = ExchangeCapabilities(
            spot=True, margin=False, futures=True, stop_orders=True, oco=True, websocket=False
        )
        self.bucket = TokenBucket(rate_per_second, max(2.0, rate_per_second * 2))

    def market(self, symbol: str) -> dict[str, Any]:
        self.bucket.acquire()
        return self.exchange.fetch_ticker(symbol)

    def validate_order(self, order_type: str) -> None:
        ot = str(order_type).upper()
        if ot in ("STOP", "STOP_LOSS", "STOP_LIMIT") and not self.capabilities.stop_orders:
            raise ValueError(f"order_type {order_type} not supported by exchange {self.exchange_id}")
        if ot in ("OCO",) and not self.capabilities.oco:
            raise ValueError(f"order_type {order_type} not supported by exchange {self.exchange_id}")

    def create_order(self, symbol: str, side: str, order_type: str, amount: Decimal, price: Decimal | None = None, params: dict | None = None):
        self.bucket.acquire()
        self.validate_order(order_type)
        return self.exchange.create_order(symbol, order_type.lower(), side.lower(), float(amount), None if price is None else float(price), params or {})


    def fetch_order(self, symbol: str, order_id: str):
        self.bucket.acquire()
        return self.exchange.fetch_order(order_id, symbol)

    def cancel_order(self, symbol: str, order_id: str):
        self.bucket.acquire()
        return self.exchange.cancel_order(order_id, symbol)
