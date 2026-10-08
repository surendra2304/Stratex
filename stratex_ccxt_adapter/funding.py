"""stratex_ccxt_adapter/funding.py

Cross-exchange perpetual futures funding rate comparator.
100% free unauthenticated queries with TTL caching; no synthetic fallback rates.
"""

from __future__ import annotations

from datetime import datetime, timezone
import logging
import threading
import time
from typing import Any, Dict, List, Optional
from .errors import MarketDataUnavailable
from .models import FundingRateComparison

logger = logging.getLogger("stratex.ccxt.funding")


class FundingRateComparator:
    """Compares perpetual contract funding rates across multiple venues."""

    def __init__(self, cache_ttl_seconds: int = 300):
        self.cache_ttl = cache_ttl_seconds
        self._cache: Dict[str, Dict[str, Any]] = {}
        self._cache_lock = threading.Lock()

    def compare(
        self,
        symbol: str = "BTCUSDT",
        exchanges: Optional[Dict[str, Any]] = None,
    ) -> FundingRateComparison:
        now = time.time()
        sym = symbol.upper()

        with self._cache_lock:
            cached = self._cache.get(sym)
            if cached and (now - cached["fetch_time"] < self.cache_ttl):
                return cached["comparison"]

        rates: Dict[str, float] = {}

        if exchanges:
            # Snapshot: the hub may register new adapters from other request
            # threads while we iterate ("dictionary changed size" -> HTTP 500).
            for ex_id, adapter in list(exchanges.items()):
                try:
                    # Check if adapter has fetch_funding_rate
                    ex = getattr(adapter, "exchange", adapter)
                    if hasattr(ex, "fetch_funding_rate"):
                        ccxt_sym = adapter.to_ccxt_symbol(symbol) if hasattr(adapter, "to_ccxt_symbol") else symbol
                        # Ensure linear/swap formatting if necessary
                        fr = ex.fetch_funding_rate(ccxt_sym)
                        if isinstance(fr, dict) and "fundingRate" in fr and fr["fundingRate"] is not None:
                            rates[ex_id] = float(fr["fundingRate"])
                except Exception as e:
                    logger.debug(f"Funding rate fetch skipped for {ex_id}: {e}")
                    continue

        if not rates:
            # Previously hardcoded "baseline" rates were returned here and served
            # as live data. No real rate means no answer.
            raise MarketDataUnavailable(f"No exchange returned a funding rate for {sym}")

        max_rate = max(rates.values())
        min_rate = min(rates.values())
        spread_bps = round((max_rate - min_rate) * 10000.0, 2)

        comparison = FundingRateComparison(
            symbol=sym,
            rates={k: round(v, 6) for k, v in rates.items()},
            max_rate=round(max_rate, 6),
            min_rate=round(min_rate, 6),
            spread_bps=spread_bps,
            timestamp_iso=datetime.now(timezone.utc).isoformat(),
        )

        with self._cache_lock:
            self._cache[sym] = {
                "fetch_time": now,
                "comparison": comparison,
            }

        return comparison
