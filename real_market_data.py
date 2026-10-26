"""Live Yahoo chart retrieval with bounded cache, partial watchlists and explicit errors."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
import json
import math
import re
import time
from typing import Callable
from urllib.parse import urlencode, quote
from urllib.request import Request, urlopen


@dataclass(frozen=True)
class MarketSnapshot:
    symbol: str
    price: float
    previous_close: float
    closes: list[float | None]
    currency: str
    high: float
    low: float
    fetched_at: float
    bars: list[dict] = field(default_factory=list)


class MarketDataClient:
    DEFAULT_TICKERS = ("^NSEI", "^BSESN", "RELIANCE.NS", "TCS.NS", "INFY.NS", "HDFCBANK.NS", "NVDA", "AAPL", "BTC-USD")

    def __init__(self, opener: Callable = urlopen, clock: Callable = time.time,
                 ttl: float = 30, timeout: float = 10) -> None:
        if not 0 <= ttl <= 3600 or not 0 < timeout <= 60:
            raise ValueError("invalid cache TTL or timeout")
        self.opener, self.clock, self.ttl, self.timeout = opener, clock, ttl, timeout
        self.cache: dict[tuple[str, str, str], MarketSnapshot] = {}

    def fetch(self, symbol: str, period: str = "5d", interval: str = "15m") -> MarketSnapshot:
        if not re.fullmatch(r"[A-Za-z0-9^=._-]{1,40}", symbol):
            raise ValueError("invalid ticker")
        if period not in {"1d", "5d", "1mo", "3mo", "1y"} or interval not in {"1m", "5m", "15m", "1h", "1d"}:
            raise ValueError("unsupported chart interval or range")
        key = (symbol, period, interval)
        cached = self.cache.get(key)
        if cached and 0 <= self.clock() - cached.fetched_at < self.ttl:
            return cached
        request = Request("https://query1.finance.yahoo.com/v8/finance/chart/" + quote(symbol, safe="") + "?" +
                          urlencode({"range": period, "interval": interval}), headers={"User-Agent": "FRIDAY-Stratex/1.0"})
        with self.opener(request, timeout=self.timeout) as response:
            raw = response.read(2*1024*1024 + 1)
        if len(raw) > 2*1024*1024:
            raise ValueError("market response exceeds size limit")
        chart = json.loads(raw).get("chart", {})
        if chart.get("error") or not chart.get("result"):
            raise ValueError("market provider returned no chart")
        result = chart["result"][0]
        meta = result.get("meta", {})
        price = meta.get("regularMarketPrice")
        previous = meta.get("chartPreviousClose") or meta.get("previousClose")
        if any(not isinstance(x, (int, float)) or isinstance(x, bool) or not math.isfinite(x) or x <= 0 for x in (price, previous)):
            raise ValueError("market price or previous close missing/invalid")
        quotes = result.get("indicators", {}).get("quote", [])
        closes = quotes[0].get("close", []) if quotes else []
        if any(x is not None and (not isinstance(x, (int, float)) or isinstance(x, bool) or not math.isfinite(x) or x <= 0) for x in closes):
            raise ValueError("invalid historical prices")
        timestamps = result.get("timestamp", [])
        opens = quotes[0].get("open", []) if quotes else []
        highs = quotes[0].get("high", []) if quotes else []
        lows = quotes[0].get("low", []) if quotes else []
        volumes = quotes[0].get("volume", []) if quotes else []
        bars = []
        for i, timestamp in enumerate(timestamps):
            try:
                o, h, l, c, v = opens[i], highs[i], lows[i], closes[i], volumes[i]
                if any(x is None or not isinstance(x, (int, float)) or isinstance(x, bool) or not math.isfinite(x) for x in (o,h,l,c,v)):
                    continue
                if min(o,h,l,c) <= 0 or v < 0 or h < max(o,c,l) or l > min(o,c,h):
                    continue
                bars.append({"timestamp": timestamp, "open": o, "high": h, "low": l, "close": c, "volume": v})
            except (IndexError, TypeError):
                continue
        snapshot = MarketSnapshot(symbol, price, previous, closes, meta.get("currency", ""),
                                  meta.get("regularMarketDayHigh", price), meta.get("regularMarketDayLow", price), self.clock(), bars)
        if len(self.cache) >= 200:
            self.cache.pop(next(iter(self.cache)))
        self.cache[key] = snapshot
        return snapshot

    def watchlist(self, symbols: list[str] | None = None) -> dict:
        symbols = list(dict.fromkeys(self.DEFAULT_TICKERS if symbols is None else symbols))
        if len(symbols) > 50:
            raise ValueError("watchlist limit is 50 tickers")
        def one(symbol):
            try:
                return symbol, self.fetch(symbol), None
            except (OSError, ValueError, KeyError, TypeError) as error:
                return symbol, None, type(error).__name__
        with ThreadPoolExecutor(max_workers=5) as pool:
            records = list(pool.map(one, symbols))
        return {"snapshots": [snapshot for _, snapshot, error in records if not error],
                "errors": {symbol: error for symbol, _, error in records if error}}
