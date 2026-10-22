"""stratex_ccxt_adapter/hub.py

Multi-Exchange Registry and Hub for CCXT.
Orchestrates connections, cross-exchange market data, arbitrage, and depth analysis.
"""

from __future__ import annotations

import logging
import threading
from typing import Any, Dict, List, Optional

from .client import CCXTExchangeAdapter
from .errors import MarketDataUnavailable
from .models import ArbitrageOpportunity, FundingRateComparison, NormalizedTicker, OrderBookDepthAnalysis
from .arbitrage import ArbitrageScanner
from .funding import FundingRateComparator
from .depth import OrderBookAnalyzer

logger = logging.getLogger("stratex.ccxt.hub")

# Supported free unauthenticated exchanges in Stratex
DEFAULT_EXCHANGE_IDS = ["binance", "okx", "bybit", "kraken", "gate"]


def _is_known_exchange(exchange_id: str) -> bool:
    try:
        import ccxt
    except Exception:  # pragma: no cover - ccxt is a declared dependency
        return False
    return exchange_id in getattr(ccxt, "exchanges", ())


class CCXTHub:
    """Singleton hub managing multi-exchange adapters and cross-venue intelligence."""

    _instance: Optional[CCXTHub] = None
    _lock = threading.Lock()

    def __new__(cls) -> CCXTHub:
        with cls._lock:
            if cls._instance is None:
                cls._instance = super().__new__(cls)
                cls._instance._exchanges = {}
                cls._instance.arbitrage_scanner = ArbitrageScanner()
                cls._instance.funding_comparator = FundingRateComparator()
                cls._instance.depth_analyzer = OrderBookAnalyzer()
            return cls._instance

    def get_exchange(self, exchange_id: str = "binance", **kwargs) -> CCXTExchangeAdapter:
        """Retrieves or creates a cached exchange adapter instance."""
        ex_id = str(exchange_id).lower().strip()
        if not _is_known_exchange(ex_id):
            raise KeyError(f"Unsupported exchange: {ex_id!r}")
        with self._lock:
            if ex_id not in self._exchanges:
                adapter = CCXTExchangeAdapter(
                    exchange_id=ex_id,
                    sandbox=kwargs.get("sandbox", True),
                    enable_rate_limit=kwargs.get("enable_rate_limit", True),
                    timeout_ms=kwargs.get("timeout_ms", 8000),
                )
                self._exchanges[ex_id] = adapter
            return self._exchanges[ex_id]

    def list_exchanges(self) -> List[str]:
        return list(DEFAULT_EXCHANGE_IDS)

    def fetch_multi_ticker(
        self,
        symbol: str = "BTCUSDT",
        exchange_ids: Optional[List[str]] = None,
    ) -> Dict[str, NormalizedTicker]:
        """Fetches normalized ticker for a symbol across multiple exchanges."""
        targets = exchange_ids or ["binance", "okx", "bybit"]
        tickers: Dict[str, NormalizedTicker] = {}

        for ex_id in targets:
            try:
                adapter = self.get_exchange(ex_id)
                t = adapter.fetch_ticker(symbol)
                tickers[ex_id] = t
            except Exception as e:
                logger.debug(f"Failed to fetch ticker for {symbol} on {ex_id}: {e}")
                continue

        # Only real quotes are returned; callers decide what an empty result means.
        return tickers

    def scan_arbitrage(
        self,
        symbol: str = "BTCUSDT",
        exchange_ids: Optional[List[str]] = None,
    ) -> ArbitrageOpportunity:
        """Compares prices across exchanges to identify arbitrage spreads."""
        tickers = self.fetch_multi_ticker(symbol, exchange_ids)
        return self.arbitrage_scanner.scan(symbol, tickers)

    def compare_funding_rates(self, symbol: str = "BTCUSDT") -> FundingRateComparison:
        """Compares perpetual funding rates across exchanges."""
        return self.funding_comparator.compare(symbol, self._exchanges)

    def analyze_depth(
        self,
        symbol: str = "BTCUSDT",
        exchange_id: str = "binance",
        depth_levels: int = 15,
    ) -> OrderBookDepthAnalysis:
        """Fetches and analyzes orderbook depth and volume-weighted micro-price."""
        adapter = self.get_exchange(exchange_id)
        try:
            raw_ob = adapter.fetch_order_book(symbol, limit=depth_levels * 2)
        except Exception as e:
            logger.debug(f"Order book fetch failed on {exchange_id}: {e}")
            raise MarketDataUnavailable(
                f"Order book for {symbol} on {exchange_id} is unavailable"
            ) from e
        if not isinstance(raw_ob, dict) or not raw_ob.get("bids") or not raw_ob.get("asks"):
            raise MarketDataUnavailable(f"Order book for {symbol} on {exchange_id} is empty")

        return self.depth_analyzer.analyze(
            order_book=raw_ob,
            symbol=symbol,
            exchange=exchange_id,
            depth_levels=depth_levels,
        )

    def get_status(self) -> Dict[str, Any]:
        """Returns overall telemetry and health for all initialized adapters."""
        with self._lock:
            adapters = list(self._exchanges.items())
        initialized = {ex_id: adapter.get_health_status() for ex_id, adapter in adapters}
        states = {info.get("status") for info in initialized.values()}
        if not initialized:
            overall = "NO_ACTIVE_ADAPTERS"
        elif "DEGRADED" in states:
            overall = "DEGRADED"
        elif states == {"HEALTHY"}:
            overall = "HEALTHY"
        else:
            overall = "UNVERIFIED"

        return {
            "status": overall,
            "active_exchanges_count": len(adapters),
            "supported_exchanges": self.list_exchanges(),
            "initialized_adapters": initialized,
            "mode": "100% Free Public / Unauthenticated Multi-Exchange",
        }


ccxt_hub = CCXTHub()
