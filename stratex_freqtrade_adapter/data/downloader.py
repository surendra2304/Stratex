"""stratex_freqtrade_adapter/data/downloader.py

Historical OHLCV data fetcher and disk cache using free public Binance REST endpoints.
Completely unauthenticated, free, and resilient with fallback data generation.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Optional
import numpy as np
import pandas as pd
import requests

logger = logging.getLogger("stratex.freqtrade.downloader")


class FreqtradeDataDownloader:
    """Downloads and caches OHLCV candles from free Binance public endpoints."""

    BINANCE_FUTURES_KLINES_URL = "https://fapi.binance.com/fapi/v1/klines"
    BINANCE_SPOT_KLINES_URL = "https://api.binance.com/api/v3/klines"

    def __init__(self, cache_dir: Optional[str] = None):
        self.cache_dir = Path(cache_dir or "data/freqtrade_cache")
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def fetch_ohlcv(
        self,
        symbol: str = "BTCUSDT",
        timeframe: str = "5m",
        limit: int = 500,
        use_cache: bool = True,
    ) -> pd.DataFrame:
        """Fetches OHLCV data as a standard pandas DataFrame.
        Columns: ['timestamp', 'open', 'high', 'low', 'close', 'volume']
        """
        cache_file = self.cache_dir / f"{symbol.upper()}_{timeframe}_{limit}.parquet"

        if use_cache and cache_file.exists():
            try:
                df = pd.read_parquet(cache_file)
                if len(df) > 0:
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
            return df

        # Fallback synthetic OHLCV if offline / unauthenticated rate limit
        logger.warning(f"Using synthetic fallback candles for {symbol} ({timeframe})")
        now = pd.Timestamp.now(tz="UTC")
        dates = pd.date_range(end=now, periods=limit, freq=timeframe)
        np.random.seed(42)
        base_price = 60000.0 if "BTC" in symbol else 3000.0
        returns = np.random.normal(0.0001, 0.005, limit)
        prices = base_price * np.exp(np.cumsum(returns))
        
        df = pd.DataFrame({
            "timestamp": dates,
            "open": prices * (1.0 - np.random.uniform(0, 0.002, limit)),
            "high": prices * (1.0 + np.random.uniform(0.001, 0.004, limit)),
            "low": prices * (1.0 - np.random.uniform(0.001, 0.004, limit)),
            "close": prices,
            "volume": np.random.uniform(10, 200, limit),
        })
        return df
