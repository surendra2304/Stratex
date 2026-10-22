"""stratex_freqtrade_adapter/pairlist/volume_pairlist.py

Dynamic pairlist based on 24-hour trading volume.
Uses completely free, unauthenticated Binance public 24hr ticker REST endpoints.
Features TTL caching and graceful offline fallback.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any, Dict, List, Optional
import requests

from .interface import IPairList

logger = logging.getLogger("stratex.freqtrade.volume_pairlist")


class VolumePairList(IPairList):
    """Dynamically sorts and selects top traded pairs by 24h quote volume."""

    BINANCE_FUTURES_TICKER_URL = "https://fapi.binance.com/fapi/v1/ticker/24hr"
    BINANCE_SPOT_TICKER_URL = "https://api.binance.com/api/v3/ticker/24hr"

    def __init__(
        self,
        number_assets: int = 20,
        sort_key: str = "quoteVolume",
        stake_currency: str = "USDT",
        refresh_period: int = 300,
        config: Optional[Dict[str, Any]] = None,
    ):
        super().__init__(config=config)
        self.number_assets = max(1, number_assets)
        self.sort_key = sort_key
        self.stake_currency = stake_currency.upper()
        self.refresh_period = refresh_period
        self._cache_lock = threading.Lock()
        self._last_fetch = 0.0
        self._cached_tickers: Dict[str, Dict[str, Any]] = {}
        self._cached_pairs: List[str] = []

    def fetch_tickers(self) -> Dict[str, Dict[str, Any]]:
        """Fetches 24h ticker data from public Binance endpoint with caching."""
        now = time.time()
        with self._cache_lock:
            if self._cached_tickers and (now - self._last_fetch) < self.refresh_period:
                return dict(self._cached_tickers)

        # Attempt public futures endpoint first, fallback to spot
        data = None
        for url in [self.BINANCE_FUTURES_TICKER_URL, self.BINANCE_SPOT_TICKER_URL]:
            try:
                resp = requests.get(url, timeout=5, headers={"User-Agent": "Stratex-Freqtrade/2.0"})
                if resp.status_code == 200:
                    data = resp.json()
                    break
            except Exception as e:
                logger.debug(f"Ticker fetch failed on {url}: {e}")
                continue

        tickers: Dict[str, Dict[str, Any]] = {}
        if isinstance(data, list):
            for item in data:
                symbol = item.get("symbol", "")
                if symbol.endswith(self.stake_currency):
                    try:
                        tickers[symbol] = {
                            "symbol": symbol,
                            "lastPrice": float(item.get("lastPrice", 0.0)),
                            "quoteVolume": float(item.get("quoteVolume", 0.0)),
                            "volume": float(item.get("volume", 0.0)),
                            "priceChangePercent": float(item.get("priceChangePercent", 0.0)),
                            "bidPrice": float(item.get("bidPrice", item.get("lastPrice", 0.0))),
                            "askPrice": float(item.get("askPrice", item.get("lastPrice", 0.0))),
                        }
                    except (ValueError, TypeError):
                        continue

        with self._cache_lock:
            if tickers:
                self._cached_tickers = tickers
                self._last_fetch = now
            elif not self._cached_tickers:
                # Baseline fallback if offline on initial startup
                fallback_symbols = [
                    "BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT",
                    "DOGEUSDT", "ADAUSDT", "AVAXUSDT", "LINKUSDT", "NEARUSDT",
                    "SUIUSDT", "APTUSDT", "INJUSDT", "DOTUSDT", "MATICUSDT"
                ]
                self._cached_tickers = {
                    s: {
                        "symbol": s,
                        "lastPrice": 100.0,
                        "quoteVolume": 1000000.0 * (15 - i),
                        "volume": 10000.0,
                        "priceChangePercent": 0.0,
                        "bidPrice": 100.0,
                        "askPrice": 100.0,
                    }
                    for i, s in enumerate(fallback_symbols)
                }
                self._last_fetch = now

            return dict(self._cached_tickers)

    def filter_pairlist(
        self,
        pairlist: List[str],
        ticker_data: Optional[Dict[str, Dict[str, Any]]] = None,
    ) -> List[str]:
        tickers = ticker_data or self.fetch_tickers()

        if pairlist:
            # Filter existing candidates
            candidates = [tickers[p] for p in pairlist if p in tickers]
        else:
            candidates = list(tickers.values())

        # Sort descending by sort_key (e.g. quoteVolume)
        candidates.sort(key=lambda x: x.get(self.sort_key, 0.0), reverse=True)

        selected = [c["symbol"] for c in candidates[: self.number_assets]]
        with self._cache_lock:
            self._cached_pairs = selected
        return selected

    def get_status(self) -> Dict[str, Any]:
        return {
            "type": "VolumePairList",
            "stake_currency": self.stake_currency,
            "number_assets": self.number_assets,
            "cached_pairs_count": len(self._cached_pairs),
            "cached_pairs": self._cached_pairs,
            "cache_age_seconds": round(time.time() - self._last_fetch, 1),
        }
