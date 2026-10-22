"""Small normalized exchange models for Stratex.
100% backwards-compatible with existing models.
"""

from __future__ import annotations
from dataclasses import dataclass
from typing import Any, Dict, List, Optional


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


@dataclass(frozen=True)
class ArbitrageOpportunity:
    """Represents a cross-exchange price divergence / arbitrage spread."""
    symbol: str
    buy_exchange: str
    buy_price: float
    sell_exchange: str
    sell_price: float
    spread: float
    spread_pct: float
    is_arbitrage_viable: bool
    timestamp_iso: str


@dataclass(frozen=True)
class OrderBookDepthAnalysis:
    """Analyzed order book depth metrics."""
    symbol: str
    exchange: str
    mid_price: float
    micro_price: float
    bid_depth_usd: float
    ask_depth_usd: float
    imbalance_ratio: float  # (bid_depth - ask_depth) / (bid_depth + ask_depth), [-1.0, 1.0]
    spread: float
    spread_bps: float
    top_bids: List[List[float]]
    top_asks: List[List[float]]


@dataclass(frozen=True)
class FundingRateComparison:
    """Comparative perpetual funding rates across exchanges."""
    symbol: str
    rates: Dict[str, float]  # exchange_id -> rate
    max_rate: float
    min_rate: float
    spread_bps: float
    timestamp_iso: str
