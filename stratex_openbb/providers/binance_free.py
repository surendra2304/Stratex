"""
stratex_openbb/providers/binance_free.py — Free Binance Market Data Provider.
Bridges Stratex's existing data_client to provide public klines, orderbook depth,
and funding rates without requiring trading credentials.
"""

import logging
from typing import Any, Dict, Optional
import pandas as pd

logger = logging.getLogger("openbb.binance")


class BinanceFreeProvider:
    """Free Binance Public Market Data Provider (reuses Stratex data_client)."""

    def __init__(self):
        self._client = None

    def _get_client(self):
        if self._client is None:
            try:
                from data_client import get_data_client
                self._client = get_data_client()
            except Exception as e:
                logger.warning(f"[OPENBB_BINANCE] Could not get data_client: {e}")
                self._client = None
        return self._client

    def get_historical_klines(
        self,
        symbol: str = "BTCUSDT",
        timeframe: str = "1h",
        limit: int = 100
    ) -> pd.DataFrame:
        """
        Fetches historical OHLCV candles as a clean pandas DataFrame.
        """
        client = self._get_client()
        if client is None:
            return pd.DataFrame()

        try:
            # Check futures or spot
            raw = None
            if hasattr(client, "futures_klines"):
                try:
                    raw = client.futures_klines(symbol=symbol, interval=timeframe, limit=limit)
                except Exception:
                    raw = None
            if not raw and hasattr(client, "get_klines"):
                raw = client.get_klines(symbol=symbol, interval=timeframe, limit=limit)

            if not raw:
                return pd.DataFrame()

            cols = [
                "timestamp", "open", "high", "low", "close", "volume",
                "close_time", "quote_volume", "trades", "taker_buy_base",
                "taker_buy_quote", "ignore"
            ]
            df = pd.DataFrame(raw, columns=cols)
            for c in ["open", "high", "low", "close", "volume"]:
                df[c] = df[c].astype(float)
            df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
            return df[["timestamp", "open", "high", "low", "close", "volume"]]
        except Exception as e:
            logger.warning(f"[OPENBB_BINANCE] Error fetching klines for {symbol}: {e}")
            return pd.DataFrame()

    def get_funding_rate(self, symbol: str = "BTCUSDT") -> Optional[float]:
        """Fetches the latest funding rate for a futures symbol."""
        client = self._get_client()
        if client is None or not hasattr(client, "futures_funding_rate"):
            return None

        try:
            res = client.futures_funding_rate(symbol=symbol, limit=1)
            if res and isinstance(res, list):
                return float(res[-1].get("fundingRate", 0.0))
        except Exception as e:
            logger.debug(f"[OPENBB_BINANCE] Could not fetch funding rate for {symbol}: {e}")
        return None

    def get_orderbook_depth(self, symbol: str = "BTCUSDT", limit: int = 20) -> Dict[str, Any]:
        """Fetches top-of-book depth and computes bid/ask imbalance."""
        client = self._get_client()
        if client is None:
            return {"symbol": symbol, "bid_volume": 0.0, "ask_volume": 0.0, "imbalance": 0.0}

        try:
            ob = None
            if hasattr(client, "futures_order_book"):
                ob = client.futures_order_book(symbol=symbol, limit=limit)
            elif hasattr(client, "get_order_book"):
                ob = client.get_order_book(symbol=symbol, limit=limit)

            if ob:
                bids = [[float(p), float(q)] for p, q in ob.get("bids", [])]
                asks = [[float(p), float(q)] for p, q in ob.get("asks", [])]
                bid_vol = sum(q for _, q in bids)
                ask_vol = sum(q for _, q in asks)
                tot = bid_vol + ask_vol
                imbalance = ((bid_vol - ask_vol) / tot) if tot > 0 else 0.0
                return {
                    "symbol": symbol,
                    "best_bid": bids[0][0] if bids else None,
                    "best_ask": asks[0][0] if asks else None,
                    "bid_volume": round(bid_vol, 4),
                    "ask_volume": round(ask_vol, 4),
                    "imbalance": round(imbalance, 4)
                }
        except Exception as e:
            logger.debug(f"[OPENBB_BINANCE] Error fetching orderbook for {symbol}: {e}")

        return {"symbol": symbol, "bid_volume": 0.0, "ask_volume": 0.0, "imbalance": 0.0}
