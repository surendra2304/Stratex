"""stratex_freqtrade_adapter/data/downloader.py

Historical OHLCV data fetcher and disk cache using free public Binance REST endpoints.
Completely unauthenticated, free, and resilient with fallback data generation.
"""

from __future__ import annotations

import logging
import os
import re
from pathlib import Path
from typing import Optional
import numpy as np
import pandas as pd
import requests

logger = logging.getLogger("stratex.freqtrade.downloader")


def to_pandas_freq(timeframe: str) -> str:
    """Convert an exchange timeframe (e.g. ``5m``, ``1h``, ``1d``) to a pandas
    offset alias that is valid across pandas 2.x and 3.x.

    pandas 3.0 removed the upper/lower-ambiguous minute alias ``m`` (and ``M``
    now means month-end only), so ``"5m"`` must become ``"5min"``. Hours
    (``h``) and days (``D``) remain valid aliases.
    """
    tf = str(timeframe).strip()
    if not tf:
        raise ValueError("empty timeframe")
    unit = tf[-1].lower()
    mult = tf[:-1] or "1"
    if not mult.isdigit():
        raise ValueError(f"invalid timeframe: {timeframe!r}")
    if unit == "m":
        return f"{mult}min"
    if unit == "h":
        return f"{mult}h"
    if unit == "d":
        return f"{mult}D"
    if unit == "w":
        return f"{mult}W"
    raise ValueError(f"unsupported timeframe: {timeframe!r}")


class OHLCVUnavailable(RuntimeError):
    """Real OHLCV data could not be obtained (and synthetic data was not requested)."""


_SYMBOL_RE = re.compile(r"^[A-Z0-9]{2,20}$")


class FreqtradeDataDownloader:
    """Downloads and caches OHLCV candles from free Binance public endpoints."""

    BINANCE_FUTURES_KLINES_URL = "https://fapi.binance.com/fapi/v1/klines"
    BINANCE_SPOT_KLINES_URL = "https://api.binance.com/api/v3/klines"

    def __init__(self, cache_dir: Optional[str] = None):
        self.cache_dir = Path(cache_dir or "data/freqtrade_cache")
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        # UNVERIFIED until a fetch happens; then LIVE / CACHE / UNAVAILABLE / SYNTHETIC.
        self.last_fetch_status = "UNVERIFIED"

    def fetch_ohlcv(
        self,
        symbol: str = "BTCUSDT",
        timeframe: str = "5m",
        limit: int = 500,
        use_cache: bool = True,
        allow_synthetic: bool = False,
    ) -> pd.DataFrame:
        """Fetches OHLCV data as a standard pandas DataFrame.
        Columns: ['timestamp', 'open', 'high', 'low', 'close', 'volume']

        Raises ``OHLCVUnavailable`` when no real data can be obtained. Synthetic
        candles are produced only when ``allow_synthetic=True`` is passed
        explicitly (offline research/tests); they used to be returned silently
        and were served as "backtests" through the API.
        """
        symbol = str(symbol).upper().strip()
        if not _SYMBOL_RE.match(symbol):
            # Also keeps the cache path inside cache_dir ("../" can't get in).
            raise ValueError(f"invalid symbol: {symbol!r}")
        to_pandas_freq(timeframe)  # validates the timeframe (raises ValueError)
        limit = int(limit)
        if limit < 1:
            raise ValueError("limit must be positive")
        cache_file = self.cache_dir / f"{symbol}_{timeframe}_{limit}.parquet"

        if use_cache and cache_file.exists():
            try:
                df = pd.read_parquet(cache_file)
                if len(df) > 0:
                    self.last_fetch_status = "CACHE"
                    return df
            except Exception:
                pass

        # Public query to Binance
        params = {
            "symbol": symbol.upper(),
            "interval": timeframe,
            "limit": min(limit, 1000),
        }

        raw_data = None
        for url in [self.BINANCE_FUTURES_KLINES_URL, self.BINANCE_SPOT_KLINES_URL]:
            try:
                resp = requests.get(url, params=params, timeout=6, headers={"User-Agent": "Stratex-Freqtrade/2.0"})
                if resp.status_code == 200:
                    raw_data = resp.json()
                    if isinstance(raw_data, list) and len(raw_data) > 0:
                        break
            except Exception as e:
                logger.debug(f"Klines fetch failed for {url}: {e}")
                continue

        if isinstance(raw_data, list) and len(raw_data) > 0:
            records = []
            for item in raw_data:
                # [time, open, high, low, close, volume, close_time, quote_vol, trades, ...]
                records.append({
                    "timestamp": pd.to_datetime(item[0], unit="ms", utc=True),
                    "open": float(item[1]),
                    "high": float(item[2]),
                    "low": float(item[3]),
                    "close": float(item[4]),
                    "volume": float(item[5]),
                })
            df = pd.DataFrame(records)
            if use_cache and len(df) > 0:
                try:
                    df.to_parquet(cache_file, index=False)
                except Exception:
                    pass
            self.last_fetch_status = "LIVE"
            return df

        if not allow_synthetic:
            self.last_fetch_status = "UNAVAILABLE"
            raise OHLCVUnavailable(f"No OHLCV data available for {symbol} ({timeframe}) from public endpoints")

        # Explicitly requested synthetic OHLCV (offline research only). Uses a
        # private RNG: reseeding the global numpy RNG leaked into other code.
        logger.warning(f"Generating explicitly requested SYNTHETIC candles for {symbol} ({timeframe})")
        rng = np.random.default_rng(42)
        now = pd.Timestamp.now(tz="UTC")
        dates = pd.date_range(end=now, periods=limit, freq=to_pandas_freq(timeframe))
        base_price = 60000.0 if "BTC" in symbol else 3000.0
        returns = rng.normal(0.0001, 0.005, limit)
        prices = base_price * np.exp(np.cumsum(returns))

        df = pd.DataFrame({
            "timestamp": dates,
            "open": prices * (1.0 - rng.uniform(0, 0.002, limit)),
            "high": prices * (1.0 + rng.uniform(0.001, 0.004, limit)),
            "low": prices * (1.0 - rng.uniform(0.001, 0.004, limit)),
            "close": prices,
            "volume": rng.uniform(10, 200, limit),
        })
        df.attrs["synthetic"] = True
        self.last_fetch_status = "SYNTHETIC"
        return df
