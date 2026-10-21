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
    # Commodities & Precious Metals
    "XAUUSD": "GC=F",
    "GOLD": "GC=F",
    "XAGUSD": "SI=F",
    "SILVER": "SI=F",
    "WTI": "CL=F",
    "USOIL": "CL=F",
    "BRENT": "BZ=F",
    "UKOIL": "BZ=F",
    # Global Indices
    "US30": "^DJI",
    "US500": "^GSPC",
    "USTEC": "^IXIC",
    "GER40": "^GDAXI",
    # Crypto CFDs
    "BTCUSD": "BTC-USD",
    "ETHUSD": "ETH-USD",
    "SOLUSD": "SOL-USD",
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

    def resolve_ticker(self, symbol: str) -> str:
        clean = symbol.strip().upper().replace("/", "")
        return SYMBOL_MAP.get(clean, f"{clean}=X")

    def get_candles(self, symbol: str, timeframe: str = "15m", limit: int = 80) -> list[dict[str, Any]]:
        """Fetch institutional OHLCV candles from cloud market feed."""
        ticker = self.resolve_ticker(symbol)
        interval = TIMEFRAME_MAP.get(timeframe.lower(), "15m")
        range_param = "5d" if interval in {"1m", "5m"} else ("1mo" if interval in {"15m", "30m", "60m"} else "3mo")

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

            return candles[-limit:]
        except Exception as exc:
            logger.debug(f"[CLOUD_FEED] Error reading {symbol} candles: {exc}")
            return []

    def get_current_price(self, symbol: str) -> float | None:
        """Fetch real-time spot quote for an asset."""
        candles = self.get_candles(symbol, timeframe="5m", limit=2)
        if candles:
            return float(candles[-1]["close"])
        return None


_FEED_INSTANCE: CloudMarketDataFeed | None = None


def get_cloud_data_feed() -> CloudMarketDataFeed:
    global _FEED_INSTANCE
    if _FEED_INSTANCE is None:
        _FEED_INSTANCE = CloudMarketDataFeed()
    return _FEED_INSTANCE
