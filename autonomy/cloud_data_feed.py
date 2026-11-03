"""
autonomy/cloud_data_feed.py — Resilient Multi-Asset Real-Time Cloud Market Data Feed.

Provides live OHLCV candle streams and tick pricing across:
- Forex Majors & Crosses
- Commodities & Precious Metals (Gold, Silver, Oil)
- Global Indices
- Crypto CFDs

Zero laptop dependency. Runs 100% autonomously in the cloud 24/7/365.
"""
from __future__ import annotations

import threading
import time
from typing import Any

import requests

from logger import get_logger

logger = get_logger("cloud_data_feed")

SYMBOL_MAP: dict[str, str] = {
    # Forex Majors
    "EURUSD": "EURUSD=X",
    "GBPUSD": "GBPUSD=X",
    "USDJPY": "USDJPY=X",
    "USDCHF": "USDCHF=X",
    "AUDUSD": "AUDUSD=X",
    "USDCAD": "USDCAD=X",
    "NZDUSD": "NZDUSD=X",
    # Euro Crosses
    "EURGBP": "EURGBP=X",
    "EURJPY": "EURJPY=X",
    "EURAUD": "EURAUD=X",
    "EURCAD": "EURCAD=X",
    "EURCHF": "EURCHF=X",
    "EURNZD": "EURNZD=X",
    # Pound Crosses
    "GBPJPY": "GBPJPY=X",
    "GBPAUD": "GBPAUD=X",
    "GBPCAD": "GBPCAD=X",
    "GBPCHF": "GBPCHF=X",
    "GBPNZD": "GBPNZD=X",
    # Yen Crosses
    "AUDJPY": "AUDJPY=X",
    "CADJPY": "CADJPY=X",
    "CHFJPY": "CHFJPY=X",
    "NZDJPY": "NZDJPY=X",
    # Commodity Pairs
    "AUDCAD": "AUDCAD=X",
    "AUDCHF": "AUDCHF=X",
    "AUDNZD": "AUDNZD=X",
    "CADCHF": "CADCHF=X",
    "NZDCAD": "NZDCAD=X",
    "NZDCHF": "NZDCHF=X",
    # Scandinavian & SGD crosses (liquid, tight spreads)
    "USDNOK": "USDNOK=X",
    "USDSEK": "USDSEK=X",
    "EURNOK": "EURNOK=X",
    "EURSEK": "EURSEK=X",
    "NOKJPY": "NOKJPY=X",
    "NOKSEK": "NOKSEK=X",
    "SEKJPY": "SEKJPY=X",
    "USDSGD": "USDSGD=X",
    "EURSGD": "EURSGD=X",
    "GBPSGD": "GBPSGD=X",
    "AUDSGD": "AUDSGD=X",
    "NZDSGD": "NZDSGD=X",
    "SGDJPY": "SGDJPY=X",
    "CHFSGD": "CHFSGD=X",
    # Commodities & Precious Metals
    "XAUUSD": "GC=F",
    "GOLD": "GC=F",
    "XAGUSD": "SI=F",
    "SILVER": "SI=F",
    "XPTUSD": "PL=F",
    "XPDUSD": "PA=F",
    "XAUEUR": "XAUEUR=X",
    "XAUGBP": "XAUGBP=X",
    # Energy
    "WTI": "CL=F",
    "USOIL": "CL=F",
    "XTIUSD": "CL=F",
    "BRENT": "BZ=F",
    "UKOIL": "BZ=F",
    "XBRUSD": "BZ=F",
    "XNGUSD": "NG=F",
    # Global Indices
    "US30": "^DJI",
    "US500": "^GSPC",
    "USTEC": "^IXIC",
    "GER40": "^GDAXI",
    "DE40": "^GDAXI",
    "UK100": "^FTSE",
    "JP225": "^N225",
    "AUS200": "^AXJO",
    "HK50": "^HSI",
    "US2000": "^RUT",
    "SWI20": "^SSMI",
    # Crypto CFDs
    "BTCUSD": "BTC-USD",
    "ETHUSD": "ETH-USD",
    "SOLUSD": "SOL-USD",
    "XRPUSD": "XRP-USD",
    "LTCUSD": "LTC-USD",
    "BNBUSD": "BNB-USD",
    "ADAUSD": "ADA-USD",
    "DOGEUSD": "DOGE-USD",
    "DOTUSD": "DOT-USD",
}

TIMEFRAME_MAP: dict[str, str] = {
    "1m": "1m",
    "5m": "5m",
    "15m": "15m",
    "30m": "30m",
    "1h": "60m",
    "4h": "1h",
    "1d": "1d",
}


class CloudMarketDataFeed:
    """Production-grade cloud market data provider requiring zero client software."""

    def __init__(self, timeout: float = 6.0):
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        })
        # Short-lived candle cache: a full-universe scan evaluates ~400
        # symbol/timeframe pairs per cycle, and refetching each one from Yahoo
        # would rate-limit the feed and stall the daemon. The cache is keyed by
        # instrument + interval so every caller (scan, marking, sizing) shares
        # the same download.
        self._cache: dict[tuple[str, str], tuple[float, list[dict[str, Any]]]] = {}
        self._cache_lock = threading.Lock()

    _CACHE_TTL = {
        "1m": 60.0, "5m": 240.0, "15m": 300.0, "30m": 600.0,
        "60m": 900.0, "1h": 900.0, "4h": 1800.0, "1d": 3600.0,
    }

    def resolve_ticker(self, symbol: str) -> str:
        clean = symbol.strip().upper().replace("/", "")
        return SYMBOL_MAP.get(clean, f"{clean}=X")

    @staticmethod
    def _resample(candles: list[dict[str, Any]], factor: int, limit: int) -> list[dict[str, Any]]:
        """Aggregate lower-timeframe candles into higher-timeframe buckets."""
        if not candles:
            return []
        bucket_seconds = factor * 3600
        buckets: list[dict[str, Any]] = []
        for candle in candles:
            bucket = int(candle["timestamp"]) // bucket_seconds
            if buckets and buckets[-1]["bucket"] == bucket:
                current = buckets[-1]
                current["high"] = max(current["high"], candle["high"])
                current["low"] = min(current["low"], candle["low"])
                current["close"] = candle["close"]
                current["volume"] += candle.get("volume", 0.0)
            else:
                buckets.append({
                    "bucket": bucket,
                    "timestamp": bucket * bucket_seconds,
                    "open": candle["open"],
                    "high": candle["high"],
                    "low": candle["low"],
                    "close": candle["close"],
                    "volume": candle.get("volume", 0.0),
                })
        return [{k: v for k, v in b.items() if k != "bucket"} for b in buckets][-limit:]

    def get_candles(self, symbol: str, timeframe: str = "15m", limit: int = 80) -> list[dict[str, Any]]:
        """Fetch institutional OHLCV candles from cloud market feed."""
        tf = timeframe.lower()
        # cTrader supports 4h/1d bars but Yahoo does not serve a 4h interval.
        # Build a true 4h series from the cached 1h stream instead of aliasing
        # 4h to 1h (which would silently duplicate the same signal twice).
        if tf == "4h":
            base = self.get_candles(symbol, "1h", limit=min(max(limit * 4, 160), 240))
            return self._resample(base, 4, limit)

        ticker = self.resolve_ticker(symbol)
        interval = TIMEFRAME_MAP.get(tf, "15m")
        cache_key = (ticker, interval)
        ttl = self._CACHE_TTL.get(interval, 300.0)
        now = time.time()
        with self._cache_lock:
            cached = self._cache.get(cache_key)
            if cached and (now - cached[0]) < ttl:
                return [dict(c) for c in cached[1]][-limit:]

        if interval in {"1m", "5m"}:
            range_param = "5d"
        elif interval in {"15m", "30m", "60m"}:
            range_param = "1mo"
        elif interval == "1d":
            range_param = "1y"
        else:
            range_param = "3mo"

        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}?interval={interval}&range={range_param}"

        try:
            resp = self.session.get(url, timeout=self.timeout)
            if resp.status_code != 200:
                logger.debug(f"[CLOUD_FEED] Failed fetching {symbol} ({ticker}): HTTP {resp.status_code}")
                return []

            data = resp.json()
            chart = data.get("chart", {}).get("result")
            if not chart:
                return []

            result = chart[0]
            timestamps = result.get("timestamp", [])
            indicators = result.get("indicators", {}).get("quote", [{}])[0]

            opens = indicators.get("open", [])
            highs = indicators.get("high", [])
            lows = indicators.get("low", [])
            closes = indicators.get("close", [])
            volumes = indicators.get("volume", [])

            candles = []
            for i in range(len(timestamps)):
                if (
                    i < len(opens) and opens[i] is not None
                    and i < len(highs) and highs[i] is not None
                    and i < len(lows) and lows[i] is not None
                    and i < len(closes) and closes[i] is not None
                ):
                    candles.append({
                        "timestamp": int(timestamps[i]),
                        "open": float(opens[i]),
                        "high": float(highs[i]),
                        "low": float(lows[i]),
                        "close": float(closes[i]),
                        "volume": float(volumes[i] if (i < len(volumes) and volumes[i] is not None) else 100.0),
                    })

            with self._cache_lock:
                # Cap retention: 350 cached series x 240 bars fits comfortably
                # inside the daemon's 300 MB systemd memory limit.
                self._cache[cache_key] = (time.time(), candles[-240:])
                if len(self._cache) > 900:
                    cutoff = time.time() - 7200
                    self._cache = {k: v for k, v in self._cache.items() if v[0] >= cutoff}
            return candles[-limit:]
        except Exception as exc:
            logger.debug(f"[CLOUD_FEED] Error reading {symbol} candles: {exc}")
            return []

    def get_current_price(self, symbol: str) -> float | None:
        """Fetch real-time spot quote for an asset (shares the scan cache)."""
        candles = self.get_candles(symbol, timeframe="5m", limit=80)
        if candles:
            return float(candles[-1]["close"])
        return None

    def prefetch(self, pairs: list[tuple[str, str]], max_workers: int = 6) -> None:
        """Warm the candle cache concurrently.

        A full-universe scan touches every symbol on every timeframe. Doing that
        serially inside the trading loop would take minutes on a cold cache and
        delay position management, so warm it with a small bounded pool.
        """
        from concurrent.futures import ThreadPoolExecutor

        def _warm(pair: tuple[str, str]) -> None:
            symbol, tf = pair
            try:
                if not self.get_candles(symbol, tf, limit=80):
                    time.sleep(0.8)
                    self.get_candles(symbol, tf, limit=80)
            except Exception:
                pass

        if not pairs:
            return
        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            list(pool.map(_warm, pairs))


_FEED_INSTANCE: CloudMarketDataFeed | None = None


def get_cloud_data_feed() -> CloudMarketDataFeed:
    global _FEED_INSTANCE
    if _FEED_INSTANCE is None:
        _FEED_INSTANCE = CloudMarketDataFeed()
    return _FEED_INSTANCE
