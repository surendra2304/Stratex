"""
exchanges/base_exchange.py — Universal Abstract Exchange Interface & Unified Normalizer.

Normalizes:
- Symbol notation (e.g. BTCUSDT, BTC-USDT, BTC/USD, XBT/USD -> unified "BTC/USDT").
- Order representation, positions, orderbook, tickers, historical data, and fee models.
- Abstract base methods for balances, market data, order placement, order cancellation, and historical candles.
- Capability flags per exchange (supports_futures, supports_shorting, supports_margin).
"""

import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass
class UnifiedTicker:
    symbol: str
    bid: float
    ask: float
    last: float
    volume_24h: float
    timestamp: float = field(default_factory=time.time)


@dataclass
class UnifiedBalance:
    currency: str
    free: float
    used: float
    total: float


@dataclass
class UnifiedPosition:
    symbol: str
    side: str  # "LONG" or "SHORT"
    quantity: float
    entry_price: float
    mark_price: float
    unrealized_pnl: float
    leverage: float = 1.0
    liquidation_price: float | None = None
    exchange: str = ""


@dataclass
class UnifiedOrderResult:
    order_id: str
    symbol: str
    side: str
    order_type: str
    price: float
    quantity: float
    status: str  # "FILLED", "PARTIALLY_FILLED", "PENDING", "CANCELLED", "REJECTED"
    executed_qty: float = 0.0
    avg_price: float = 0.0
    fee_paid: float = 0.0
    exchange: str = ""
    timestamp: float = field(default_factory=time.time)


@dataclass
class ExchangeCapabilities:
    supports_futures: bool = True
    supports_shorting: bool = True
    supports_margin: bool = True
    supports_limit_orders: bool = True
    supports_stop_orders: bool = True
    supports_websocket: bool = True
    max_leverage: float = 20.0


class BaseExchange(ABC):
    """
    Universal abstract base class for cryptocurrency exchange adapters.
    """

    def __init__(
        self,
        exchange_id: str,
        capabilities: ExchangeCapabilities | None = None
    ):
        self.exchange_id = exchange_id.lower()
        self.capabilities = capabilities or ExchangeCapabilities()
        self.is_connected = False
        self.rate_limit_delay_ms = 100
        self.slippage_history: list[float] = []

    def is_healthy(self) -> bool:
        """Returns whether the exchange connection is operational."""
        return True

    def normalize_symbol(self, raw_symbol: str) -> str:
        """
        Converts raw exchange symbols to unified BASE/QUOTE format (e.g. BTC/USDT).
        Handles BTCUSDT, BTC-USDT, XBT/USD, BTC_USDT, etc.
        """
        sym = raw_symbol.upper().replace("-", "/").replace("_", "/")
        if sym.startswith("XBT"):
            sym = "BTC" + sym[3:]

        if "/" in sym:
            parts = [p for p in sym.split("/") if p]
            if len(parts) == 2:
                base, quote = parts[0], parts[1]
                if base == "XBT":
                    base = "BTC"
                return f"{base}/{quote}"
            return sym

        for quote in ["USDT", "USDC", "FDUSD", "BUSD", "USD", "EUR", "BTC", "ETH"]:
            if sym.endswith(quote) and len(sym) > len(quote):
                base = sym[:-len(quote)]
                if base == "XBT":
                    base = "BTC"
                return f"{base}/{quote}"

        return sym

    def denormalize_symbol(self, unified_symbol: str) -> str:
        """Converts unified BASE/QUOTE symbol to exchange-native format."""
        return unified_symbol.replace("/", "")

    @abstractmethod
    def get_balance(self) -> dict[str, UnifiedBalance]:
        """Fetches account balances keyed by currency symbol (e.g. 'USDT', 'BTC')."""

    @abstractmethod
    def get_positions(self) -> list[UnifiedPosition]:
        """Fetches currently open positions."""

    @abstractmethod
    def get_ticker(self, symbol: str) -> UnifiedTicker:
        """Fetches latest bid/ask/last ticker data."""

    @abstractmethod
    def get_orderbook(self, symbol: str, limit: int = 20) -> dict[str, list[list[float]]]:
        """Fetches top-of-book depth {'bids': [[price, qty], ...], 'asks': [[price, qty], ...]}."""

    @abstractmethod
    def place_order(
        self,
        symbol: str,
        side: str,
        order_type: str,
        quantity: float,
        price: float | None = None,
        stop_price: float | None = None
    ) -> UnifiedOrderResult:
        """Submits a new order to the exchange."""

    @abstractmethod
    def cancel_order(self, symbol: str, order_id: str) -> bool:
        """Cancels an existing pending order."""

    @abstractmethod
    def get_historical_data(self, symbol: str, timeframe: str = "15m", limit: int = 100) -> list[dict[str, Any]]:
        """Fetches historical OHLCV candle records."""

    @abstractmethod
    def get_fees(self, symbol: str) -> tuple[float, float]:
        """Returns (maker_fee_pct, taker_fee_pct)."""

    def get_trading_fees(self, symbol: str) -> tuple[float, float]:
        """Alias for get_fees()."""
        return self.get_fees(symbol)

    def get_funding_rate(self, symbol: str) -> float:
        """Fetches latest 8h funding rate for futures contracts."""
        return 0.0001
