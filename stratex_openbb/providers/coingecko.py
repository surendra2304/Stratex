"""
stratex_openbb/providers/coingecko.py — Free CoinGecko Data Provider.
Fetches global market cap, 24h volume, BTC/ETH dominance, and trending coins without an API key.
"""

import datetime
import json
import logging
import threading
import time
from typing import Any, Dict, List, Optional

import requests

from stratex_openbb.models import CryptoGlobalOverview

logger = logging.getLogger("openbb.coingecko")

_CACHE_TTL_SECONDS = 300  # 5 minutes cache to respect free public rate limits
_COINGECKO_BASE = "https://api.coingecko.com/api/v3"


class FreeCoinGeckoProvider:
    """100% Free Public CoinGecko Provider."""

    def __init__(self, timeout: float = 6.0):
        self.timeout = timeout
        self._lock = threading.Lock()
        self._cached_overview: Optional[CryptoGlobalOverview] = None
        self._overview_cached_at: float = 0.0
        self._cached_trending: List[Dict[str, Any]] = []
        self._trending_cached_at: float = 0.0

    def get_global_overview(self, force_refresh: bool = False) -> CryptoGlobalOverview:
        """
        Fetches global crypto market overview (market cap, volume, BTC/ETH dominance).
        Thread-safe with TTL cache and graceful fallback.
        """
        now = time.time()
        with self._lock:
            if not force_refresh and self._cached_overview and (now - self._overview_cached_at < _CACHE_TTL_SECONDS):
                return self._cached_overview

        url = f"{_COINGECKO_BASE}/global"
        headers = {
            "Accept": "application/json",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Stratex-OpenBB/1.0"
        }

        try:
            resp = requests.get(url, headers=headers, timeout=self.timeout)
            if resp.status_code == 200:
                raw = resp.json().get("data", {})
                total_mc = float(raw.get("total_market_cap", {}).get("usd", 0.0))
                total_vol = float(raw.get("total_volume", {}).get("usd", 0.0))
                mc_pct = raw.get("market_cap_percentage", {})
                btc_dom = float(mc_pct.get("btc", 0.0))
                eth_dom = float(mc_pct.get("eth", 0.0))
                active_coins = int(raw.get("active_cryptocurrencies", 0))
                mc_change_24h = float(raw.get("market_cap_change_percentage_24h_usd", 0.0))

                overview = CryptoGlobalOverview(
                    total_market_cap_usd=round(total_mc, 2),
                    total_volume_24h_usd=round(total_vol, 2),
                    btc_dominance_pct=round(btc_dom, 2),
                    eth_dominance_pct=round(eth_dom, 2),
                    active_cryptocurrencies=active_coins,
                    market_cap_change_pct_24h=round(mc_change_24h, 3),
                    updated_at=datetime.datetime.now(datetime.timezone.utc).isoformat(),
                    source="CoinGecko (Public Free API)"
                )
                with self._lock:
                    self._cached_overview = overview
                    self._overview_cached_at = now
                logger.debug(f"[OPENBB_COINGECKO] Global overview updated: MC=${total_mc:,.0f} BTC Dom={btc_dom:.1f}%")
                return overview
            elif resp.status_code == 429:
                logger.warning("[OPENBB_COINGECKO] CoinGecko free rate limit reached (HTTP 429). Using cache/fallback.")
        except Exception as e:
            logger.warning(f"[OPENBB_COINGECKO] Error querying CoinGecko global endpoint: {e}")

        # Return existing cache if available
        with self._lock:
            if self._cached_overview:
                return self._cached_overview

        # Fallback approximation if first call offline
        fallback = CryptoGlobalOverview(
            total_market_cap_usd=2_500_000_000_000.0,
            total_volume_24h_usd=85_000_000_000.0,
            btc_dominance_pct=55.0,
            eth_dominance_pct=15.0,
            active_cryptocurrencies=14_000,
            market_cap_change_pct_24h=0.0,
            updated_at=datetime.datetime.now(datetime.timezone.utc).isoformat(),
            source="Offline Fallback"
        )
        return fallback

    def get_trending_coins(self, force_refresh: bool = False) -> List[Dict[str, Any]]:
        """Fetches top trending coins from CoinGecko public API."""
        now = time.time()
        with self._lock:
            if not force_refresh and self._cached_trending and (now - self._trending_cached_at < _CACHE_TTL_SECONDS):
                return list(self._cached_trending)

        url = f"{_COINGECKO_BASE}/search/trending"
        headers = {
            "Accept": "application/json",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Stratex-OpenBB/1.0"
        }

        try:
            resp = requests.get(url, headers=headers, timeout=self.timeout)
            if resp.status_code == 200:
                data = resp.json()
                coins_raw = data.get("coins", [])
                result = []
                for entry in coins_raw[:10]:
                    item = entry.get("item", {})
                    result.append({
                        "id": item.get("id"),
                        "name": item.get("name"),
                        "symbol": str(item.get("symbol", "")).upper(),
                        "market_cap_rank": item.get("market_cap_rank"),
                        "score": item.get("score"),
                        "price_btc": item.get("price_btc")
                    })
                with self._lock:
                    self._cached_trending = result
                    self._trending_cached_at = now
                return result
        except Exception as e:
            logger.warning(f"[OPENBB_COINGECKO] Error querying CoinGecko trending endpoint: {e}")

        with self._lock:
            return list(self._cached_trending)
