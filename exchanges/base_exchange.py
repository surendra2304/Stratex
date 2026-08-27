"""
exchanges/base_exchange.py — Universal Abstract Exchange Interface & Unified Normalizer.

Normalizes:
- Symbol notation (e.g. BTCUSDT, BTC-USDT, BTC/USD -> unified "BTC/USDT").
- Order representation, positions, orderbook, tickers, and fee models.
- Abstract base methods for balances, market data, order placement, order cancellation, and funding rates.
"""

from abc import ABC, abstractmethod
from typing import Dict, List, Optional, Tuple, Any
from dataclasses import dataclass, field
import time


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
    liquidation_price: Optional[float] = None


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
    timestamp: float = field(default_factory=time.time)


class BaseExchange(ABC):
    """
    Universal abstract base class for cryptocurrency exchange adapters.
    """

    def __init__(self, exchange_id: str):
        self.exchange_id = exchange_id.lower()
        self.is_connected = False
        self.rate_limit_delay_ms = 100

    def is_healthy(self) -> bool:
        """Returns whether the exchange connection is operational."""
        return True

    def normalize_symbol(self, raw_symbol: str) -> str:
        """Converts raw exchange symbols to unified BASE/QUOTE format (e.g. BTC/USDT)."""
        sym = raw_symbol.upper().replace("-", "/").replace("_", "")
        if "/" not in sym:
            # e.g. BTCUSDT -> BTC/USDT
            if sym.endswith("USDT"):
                return f"{sym[:-4]}/USDT"
            elif sym.endswith("BUSD"):
                return f"{sym[:-4]}/BUSD"
            elif sym.endswith("USD"):
                return f"{sym[:-3]}/USD"
        return sym

    def denormalize_symbol(self, unified_symbol: str) -> str:
        """Converts unified BASE/QUOTE symbol to exchange-native format."""
        # Default fallback: strip slash (e.g. BTC/USDT -> BTCUSDT)
        return unified_symbol.replace("/", "")

    @abstractmethod
    def get_balance(self) -> Dict[str, UnifiedBalance]:
        """Fetches account balances."""
        pass

    @abstractmethod
    def get_positions(self) -> List[UnifiedPosition]:
        """Fetches currently open positions."""
        pass

    @abstractmethod
    def get_ticker(self, symbol: str) -> UnifiedTicker:
        """Fetches latest bid/ask/last ticker data."""
        pass

    @abstractmethod
    def get_orderbook(self, symbol: str, limit: int = 20) -> Dict[str, List[List[float]]]:
        """Fetches top-of-book depth {bids: [[p, q]], asks: [[p, q]]}."""
        pass

    @abstractmethod
    def place_order(
        self,
        symbol: str,
        side: str,
        order_type: str,
        quantity: float,
        price: Optional[float] = None,
        stop_price: Optional[float] = None
    ) -> UnifiedOrderResult:
        """Submits a new order to the exchange."""
        pass

    @abstractmethod
    def cancel_order(self, symbol: str, order_id: str) -> bool:
        """Cancels an existing pending order."""
        pass

    @abstractmethod
    def get_funding_rate(self, symbol: str) -> float:
        """Fetches latest 8h funding rate for futures contracts."""
        pass

    @abstractmethod
    def get_trading_fees(self, symbol: str) -> Tuple[float, float]:
        """Returns (maker_fee_pct, taker_fee_pct)."""
        pass
