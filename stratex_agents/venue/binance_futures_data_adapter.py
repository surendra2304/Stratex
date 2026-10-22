"""Read-only Binance USDⓈ-M Futures market-data adapter.

This adapter is intentionally separate from Binance order execution. It uses an
unauthenticated client pinned to the selected Demo Trading or legacy Futures
Testnet environment, and never falls back to production prices or metadata.
"""
from __future__ import annotations

import os
from typing import Any

from stratex_agents.venue.binance_futures_agent import BinanceFuturesAgent


class BinanceMarketDataError(RuntimeError):
    """Market data was missing, invalid, or came from an unsupported environment."""


class BinanceFuturesMarketDataAdapter:
    venue = "binance"

    def __init__(self, client: Any | None = None, venue_agent: BinanceFuturesAgent | None = None):
        self.venue_agent = venue_agent or BinanceFuturesAgent()
        self.environment = "binance_demo" if self.venue_agent.demo_trading else "binance_testnet"
        self.client = client or self.venue_agent.create_public_client(futures=True)
        actual = getattr(self.client, "stratex_environment", None)
        expected = "demo_trading_public" if self.venue_agent.demo_trading else "legacy_testnet_public"
        if actual != expected:
            raise BinanceMarketDataError(
                f"Market-data environment mismatch or unverified client: expected {expected!r}, got {actual!r}"
            )

    def list_instruments(self, query: str = "", limit: int = 1000) -> list[dict[str, Any]]:
        bounded_limit = max(1, min(5000, int(limit)))
        try:
            payload = self.client.futures_exchange_info()
        except Exception as exc:
            raise BinanceMarketDataError("Cannot load symbol metadata from the selected Binance demo/testnet environment.") from exc
        query_lower = str(query).strip().casefold()
        instruments = []
        for item in payload.get("symbols", []):
            symbol = str(item.get("symbol", ""))
            description = f"{symbol} {item.get('baseAsset', '')} {item.get('quoteAsset', '')}"
            if query_lower and query_lower not in description.casefold():
                continue
            filters = {f.get("filterType"): f for f in item.get("filters", [])}
            lot = filters.get("LOT_SIZE") or {}
            price = filters.get("PRICE_FILTER") or {}
            notional = filters.get("MIN_NOTIONAL") or filters.get("NOTIONAL") or {}
            instruments.append({
                "venue": self.venue,
                "environment": self.environment,
                "symbol": symbol,
                "description": description,
                "path": "Crypto/Perpetual Futures",
                "asset_class": "crypto_perpetual",
                "base_currency": str(item.get("baseAsset", "")),
                "quote_currency": str(item.get("quoteAsset", "")),
                "margin_currency": str(item.get("marginAsset", "")),
                "contract_size": 1.0,
                "digits": int(item.get("pricePrecision", 0) or 0),
                "tick_size": float(price.get("tickSize", 0) or 0),
                "volume_min": float(lot.get("minQty", 0) or 0),
                "volume_max": float(lot.get("maxQty", 0) or 0),
                "volume_step": float(lot.get("stepSize", 0) or 0),
                "min_notional": float(notional.get("notional", notional.get("minNotional", 0)) or 0),
                "tradeable": str(item.get("status", "")).upper() == "TRADING",
                "visible": True,
            })
        instruments.sort(key=lambda row: row["symbol"])
        return instruments[:bounded_limit]

    def get_quote(self, symbol: str) -> dict[str, Any]:
        market = symbol.replace("/", "").strip().upper()
        if not market:
            raise ValueError("symbol is required")
        try:
            book = self.client.futures_orderbook_ticker(symbol=market)
            last = self.client.futures_symbol_ticker(symbol=market)
        except Exception as exc:
            raise BinanceMarketDataError(f"Cannot fetch quote for {market} from {self.environment}.") from exc
        bid = float(book.get("bidPrice", 0) or 0)
        ask = float(book.get("askPrice", 0) or 0)
        last_price = float(last.get("price", 0) or 0)
        if min(bid, ask, last_price) <= 0 or ask < bid:
            raise BinanceMarketDataError(f"Binance returned invalid bid/ask/last values for {market}.")
        return {
            "venue": self.venue, "environment": self.environment, "symbol": market,
            "bid": bid, "ask": ask, "last": last_price, "spread": ask - bid,
            "timestamp_ms": None,
        }

    def get_candles(self, symbol: str, timeframe: str = "15m", limit: int = 300) -> list[dict[str, Any]]:
        market = symbol.replace("/", "").strip().upper()
        bounded_limit = max(1, min(1500, int(limit)))
        try:
            rows = self.client.futures_klines(symbol=market, interval=timeframe, limit=bounded_limit)
        except Exception as exc:
            raise BinanceMarketDataError(
                f"Cannot fetch {timeframe} candles for {market} from {self.environment}."
            ) from exc
        result = []
        for row in rows:
            if not isinstance(row, (list, tuple)) or len(row) < 6:
                continue
            result.append({
                "venue": self.venue, "environment": self.environment, "symbol": market,
                "timeframe": timeframe, "timestamp_ms": int(row[0]),
                "open": float(row[1]), "high": float(row[2]), "low": float(row[3]),
                "close": float(row[4]), "volume": float(row[5]),
            })
        return result
