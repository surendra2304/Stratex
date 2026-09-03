"""Small normalized exchange models for Stratex."""

from dataclasses import dataclass
from typing import Any

@dataclass(frozen=True)
class NormalizedMarket:
    symbol: str
    base: str
    quote: str
    active: bool
    market_type: str
    min_amount: float | None = None
    max_amount: float | None = None
    min_cost: float | None = None
    price_precision: int | None = None
    amount_precision: int | None = None
    price_step: float | None = None
    amount_step: float | None = None

@dataclass(frozen=True)
class NormalizedTicker:
    symbol: str
    last: float | None
    bid: float | None
    ask: float | None
    base_volume: float | None
    quote_volume: float | None
    timestamp_ms: int | None

@dataclass(frozen=True)
class NormalizedOrder:
    id: str
    client_order_id: str | None
    symbol: str
    side: str
    order_type: str
    status: str
    amount: float | None
    filled: float | None
    remaining: float | None
    average: float | None
    price: float | None
    cost: float | None
    fee: dict[str, Any] | None
    timestamp_ms: int | None
    raw: dict[str, Any] | None = None
