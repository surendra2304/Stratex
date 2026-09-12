"""
stratex_openbb/providers/macro_free.py — Free Macroeconomic Data Provider.
Extracts cross-asset macroeconomic indicators (DXY, 10Y Treasury, VIX, S&P 500, Gold)
from public web endpoints without paid subscriptions or third-party SDK dependencies.
"""

import datetime
import logging
import threading
import time
from typing import Dict, List, Optional

import requests

from stratex_openbb.models import MacroIndicatorSnapshot

logger = logging.getLogger("openbb.macro")

_CACHE_TTL_SECONDS = 300  # 5 minutes
_YAHOO_CHART_BASE = "https://query1.finance.yahoo.com/v8/finance/chart"

# Canonical free macro symbols
MACRO_SYMBOLS = {
    "DXY": {"symbol": "DX-Y.NYB", "name": "US Dollar Index"},
    "US10Y": {"symbol": "^TNX", "name": "US 10-Year Treasury Yield"},
    "VIX": {"symbol": "^VIX", "name": "CBOE Volatility Index"},
    "SP500": {"symbol": "^GSPC", "name": "S&P 500 Index"},
    "GOLD": {"symbol": "GC=F", "name": "Gold Futures"},
    "OIL": {"symbol": "CL=F", "name": "Crude Oil Futures"}
}

# Reliable baseline fallbacks for offline / test environments
_BASELINE_FALLBACKS = {
    "DX-Y.NYB": 104.2,
    "^TNX": 4.15,
    "^VIX": 15.5,
    "^GSPC": 5600.0,
    "GC=F": 2500.0,
    "CL=F": 72.0
}


class FreeMacroProvider:
    """100% Free Public Macroeconomic & Cross-Asset Provider."""

    def __init__(self, timeout: float = 6.0):
        self.timeout = timeout
        self._lock = threading.Lock()
        self._cache: Dict[str, MacroIndicatorSnapshot] = {}
        self._cache_times: Dict[str, float] = {}

    def get_indicator(self, key: str, force_refresh: bool = False) -> MacroIndicatorSnapshot:
        """
        Fetches a single macroeconomic indicator by key (DXY, US10Y, VIX, SP500, GOLD, OIL)
        or symbol name.
        """
        key_upper = key.upper()
        spec = MACRO_SYMBOLS.get(key_upper)
        if not spec:
            # Check if key is raw symbol
            for k, s in MACRO_SYMBOLS.items():
                if s["symbol"] == key_upper:
                    spec = s
                    break
        if not spec:
            spec = {"symbol": key, "name": key}

        sym = spec["symbol"]
        name = spec["name"]
        now = time.time()

        with self._lock:
            cached = self._cache.get(sym)
            cached_at = self._cache_times.get(sym, 0.0)
            if not force_refresh and cached and (now - cached_at < _CACHE_TTL_SECONDS):
                return cached

        # Fetch live data via public chart endpoint
        url = f"{_YAHOO_CHART_BASE}/{sym}?interval=1d&range=5d"
        headers = {
            "Accept": "application/json",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Stratex-OpenBB/1.0"
        }

        try:
            resp = requests.get(url, headers=headers, timeout=self.timeout)
            if resp.status_code == 200:
                body = resp.json()
                result = body.get("chart", {}).get("result", [])
                if result:
                    meta = result[0].get("meta", {})
                    current_val = float(meta.get("regularMarketPrice", 0.0))
                    prev_close = float(meta.get("previousClose", meta.get("chartPreviousClose", current_val)))
                    
                    if current_val <= 0:
                        # Try closing prices array
                        quotes = result[0].get("indicators", {}).get("quote", [{}])[0].get("close", [])
                        valid_quotes = [q for q in quotes if q is not None]
                        if valid_quotes:
                            current_val = float(valid_quotes[-1])
                            prev_close = float(valid_quotes[-2]) if len(valid_quotes) > 1 else current_val

                    chg_pct = ((current_val - prev_close) / prev_close * 100.0) if prev_close > 0 else 0.0
                    trend = "BULLISH" if chg_pct > 0.15 else ("BEARISH" if chg_pct < -0.15 else "NEUTRAL")

                    snapshot = MacroIndicatorSnapshot(
                        symbol=sym,
                        name=name,
                        current_value=round(current_val, 4),
                        change_pct=round(chg_pct, 2),
                        trend=trend,
                        timestamp=datetime.datetime.now(datetime.timezone.utc).isoformat(),
                        source="Public Market Data"
                    )
                    with self._lock:
                        self._cache[sym] = snapshot
                        self._cache_times[sym] = now
                    logger.debug(f"[OPENBB_MACRO] Updated {name} ({sym}): {current_val} ({chg_pct:+.2f}%)")
                    return snapshot
        except Exception as e:
            logger.warning(f"[OPENBB_MACRO] Error querying {sym}: {e}")

        # Check existing cache
        with self._lock:
            if sym in self._cache:
                return self._cache[sym]

        # Graceful fallback
        fallback_val = _BASELINE_FALLBACKS.get(sym, 100.0)
        snapshot = MacroIndicatorSnapshot(
            symbol=sym,
            name=name,
            current_value=fallback_val,
            change_pct=0.0,
            trend="NEUTRAL",
            timestamp=datetime.datetime.now(datetime.timezone.utc).isoformat(),
            source="Offline Fallback"
        )
        with self._lock:
            self._cache[sym] = snapshot
            self._cache_times[sym] = now
        return snapshot

    def get_all_macro_indicators(self, force_refresh: bool = False) -> Dict[str, MacroIndicatorSnapshot]:
        """Returns snapshots for all standard macro indicators."""
        results = {}
        for key in MACRO_SYMBOLS:
            results[key] = self.get_indicator(key, force_refresh=force_refresh)
        return results
