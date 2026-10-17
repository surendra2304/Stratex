"""Venue-aware market data normalization for Stratex.

Broker adapters own raw API semantics. This agent normalizes their output without
renaming broker symbols or conflating Binance crypto contracts with MT5 CFDs.
Execution decisions remain outside the market-data layer.
"""
from __future__ import annotations

import os
import time
from dataclasses import asdict, dataclass, field
from typing import Any


class MarketDataUnavailable(RuntimeError):
    """A requested venue or symbol could not be resolved against broker data."""


@dataclass(frozen=True)
class NormalizedInstrument:
    venue: str
    environment: str
    symbol: str
    display_name: str
    asset_class: str
    base_currency: str
    quote_currency: str
    margin_currency: str
    contract_size: float
    min_quantity: float
    max_quantity: float
    quantity_step: float
    price_digits: int
    tick_size: float
    tick_value: float
    tradeable: bool
    visible: bool
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class NormalizedQuote:
    venue: str
    environment: str
    symbol: str
    bid: float
    ask: float
    last: float
    spread: float
    source_timestamp_ms: int | None
    received_at: float

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class NormalizedCandle:
    venue: str
    environment: str
    symbol: str
    timeframe: str
    timestamp_ms: int
    open: float
    high: float
    low: float
    close: float
    volume: float

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _asset_class(raw: dict[str, Any], venue: str) -> str:
    existing = str(raw.get("asset_class", "")).strip().lower()
    if existing:
        return existing
    path = " ".join((str(raw.get("path", "")), str(raw.get("description", "")))).casefold()
    if venue == "binance":
        return "crypto_perpetual"
    if any(token in path for token in ("forex", "fx ", "currencies", "currency pair")):
        return "forex"
    if any(token in path for token in ("metal", "gold", "silver", "commodity", "commodities", "oil", "energy")):
        return "commodity_cfd"
    if any(token in path for token in ("index", "indices", "indices")):
        return "index_cfd"
    if any(token in path for token in ("stock", "stocks", "share", "shares", "equity")):
        return "equity_cfd"
    if any(token in path for token in ("crypto", "cryptocurrency", "digital asset")):
        return "crypto_cfd"
    return "other_cfd"


def _finite_number(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
        if number != number or number in (float("inf"), float("-inf")):
            return default
        return number
    except (TypeError, ValueError):
        return default


class MarketDataAgent:
    """Normalize instruments, quotes and candles by explicitly named venue."""

    agent_id = "market_data"

    def __init__(self, providers: dict[str, Any]):
        self.providers = {key.strip().lower(): value for key, value in providers.items()}
        unsupported = set(self.providers) - {"binance", "mt5"}
        if unsupported:
            raise ValueError(f"Unsupported Stratex market-data venues: {sorted(unsupported)}")

    @classmethod
    def from_environment(cls, *, mt5_adapter: Any | None = None) -> "MarketDataAgent":
        """Construct read-only providers for only the selected non-production environments."""
        from stratex_agents.venue.binance_futures_data_adapter import BinanceFuturesMarketDataAdapter

        providers: dict[str, Any] = {}
        # The Binance data adapter pins itself to current Demo Trading or legacy Futures Testnet.
        # If adapter initialization fails, surface that failure to the caller;
        # never silently omit a venue and imply discovery was complete.
        providers["binance"] = BinanceFuturesMarketDataAdapter()

        if mt5_adapter is None and os.getenv("MT5_BRIDGE_ENABLED", "false").strip().lower() in {"1", "true", "yes", "on"}:
            from exchanges.mt5_bridge_adapter import MT5BridgeAdapter
            mt5_adapter = MT5BridgeAdapter()
        if mt5_adapter is not None:
            providers["mt5"] = mt5_adapter
        return cls(providers)

    def health(self) -> dict[str, Any]:
        return {
            "agent_id": self.agent_id,
            "status": "ready" if self.providers else "no_market_data_venues_configured",
            "venues": sorted(self.providers),
            "execution_authority": False,
        }

    @staticmethod
    def _normalize_instrument(raw: dict[str, Any], venue: str) -> NormalizedInstrument:
        environment = str(raw.get("environment") or ("mt5_demo" if venue == "mt5" else "binance_testnet"))
        symbol = str(raw.get("symbol", "")).strip()
        if not symbol:
            raise ValueError("broker instrument metadata is missing its native symbol")
        base = str(raw.get("base_currency") or "")
        quote = str(raw.get("quote_currency") or raw.get("profit_currency") or "")
        margin = str(raw.get("margin_currency") or quote)
        is_tradeable = raw.get("tradeable")
        if is_tradeable is None:
            try:
                # MT5 SYMBOL_TRADE_MODE: DISABLED=0, LONGONLY=1, SHORTONLY=2,
                # CLOSEONLY=3, FULL=4. CLOSEONLY is not executable for new entries.
                is_tradeable = int(raw.get("trade_mode", 0)) in {1, 2, 4}
            except (TypeError, ValueError):
                is_tradeable = False
        known_fields = {
            "venue", "environment", "symbol", "description", "path", "asset_class",
            "base_currency", "quote_currency", "profit_currency", "margin_currency",
            "contract_size", "volume_min", "volume_max", "volume_step", "digits",
            "tick_size", "tick_value", "tradeable", "visible", "trade_mode",
        }
        metadata = {key: value for key, value in raw.items() if key not in known_fields}
        return NormalizedInstrument(
            venue=venue,
            environment=environment,
            symbol=symbol,  # preserve broker-native suffixes such as XAUUSD.a exactly
            display_name=str(raw.get("description") or symbol),
            asset_class=_asset_class(raw, venue),
            base_currency=base,
            quote_currency=quote,
            margin_currency=margin,
            contract_size=_finite_number(raw.get("contract_size")),
            min_quantity=_finite_number(raw.get("volume_min")),
            max_quantity=_finite_number(raw.get("volume_max")),
            quantity_step=_finite_number(raw.get("volume_step")),
            price_digits=int(_finite_number(raw.get("digits"))),
            tick_size=_finite_number(raw.get("tick_size")),
            tick_value=_finite_number(raw.get("tick_value")),
            tradeable=bool(is_tradeable),
            visible=bool(raw.get("visible", False)),
            metadata=metadata,
        )

    def list_instruments(
        self,
        *,
        venue: str | None = None,
        query: str = "",
        asset_class: str | None = None,
        limit: int = 1000,
        visible_only: bool = False,
    ) -> dict[str, Any]:
        selected = venue.strip().lower() if venue else None
        if selected is not None and selected not in self.providers:
            raise MarketDataUnavailable(f"Market-data venue {selected!r} is not configured.")
        venues = [selected] if selected else sorted(self.providers)
        bounded_limit = max(1, min(5000, int(limit)))
        instruments: list[NormalizedInstrument] = []
        providers: list[dict[str, Any]] = []
        for venue_name in venues:
            provider = self.providers[venue_name]
            try:
                if venue_name == "mt5":
                    raw_rows = provider.get_symbols(
                        query=query, visible_only=visible_only, limit=bounded_limit
                    )
                else:
                    raw_rows = provider.list_instruments(query=query, limit=bounded_limit)
                normalized = [self._normalize_instrument(row, venue_name) for row in raw_rows]
                providers.append({"venue": venue_name, "status": "ok", "count": len(normalized)})
                instruments.extend(normalized)
            except Exception as exc:
                providers.append({
                    "venue": venue_name, "status": "unavailable",
                    "error": type(exc).__name__,
                })
        requested_asset = str(asset_class or "").strip().lower()
        if requested_asset:
            instruments = [item for item in instruments if item.asset_class == requested_asset]
        instruments.sort(key=lambda item: (item.venue, item.symbol.casefold()))
        return {
            "instruments": [item.to_dict() for item in instruments[:bounded_limit]],
            "providers": providers,
            "partial": any(provider["status"] != "ok" for provider in providers),
            "count": min(len(instruments), bounded_limit),
        }

    def get_quote(self, venue: str, symbol: str) -> dict[str, Any]:
        venue_name = venue.strip().lower()
        provider = self.providers.get(venue_name)
        if provider is None:
            raise MarketDataUnavailable(f"Market-data venue {venue_name!r} is not configured.")
        raw = provider.get_quote(symbol) if venue_name == "binance" else None
        if venue_name == "mt5":
            quote = provider.get_ticker(symbol)
            raw = {
                "venue": "mt5",
                "environment": "mt5_demo",
                "symbol": quote.symbol,
                "bid": quote.bid,
                "ask": quote.ask,
                "last": quote.last,
                "spread": quote.ask - quote.bid,
                "timestamp_ms": None,
            }
        bid = _finite_number(raw.get("bid"), -1)
        ask = _finite_number(raw.get("ask"), -1)
        last = _finite_number(raw.get("last"), -1)
        if bid <= 0 or ask <= 0 or last <= 0 or ask < bid:
            raise MarketDataUnavailable(f"Venue returned an invalid quote for {venue_name}:{symbol}.")
        return NormalizedQuote(
            venue=venue_name,
            environment=str(raw.get("environment") or ("mt5_demo" if venue_name == "mt5" else "binance_testnet")),
            symbol=str(raw.get("symbol") or symbol),
            bid=bid, ask=ask, last=last,
            spread=_finite_number(raw.get("spread"), ask - bid),
            source_timestamp_ms=int(raw["timestamp_ms"]) if raw.get("timestamp_ms") else None,
            received_at=time.time(),
        ).to_dict()

    def get_candles(
        self, venue: str, symbol: str, timeframe: str = "15m", limit: int = 300
    ) -> list[dict[str, Any]]:
        venue_name = venue.strip().lower()
        provider = self.providers.get(venue_name)
        if provider is None:
            raise MarketDataUnavailable(f"Market-data venue {venue_name!r} is not configured.")
        if venue_name == "mt5":
            raw_rows = provider.get_historical_data(symbol, timeframe=timeframe, limit=limit)
            environment = "mt5_demo"
        else:
            raw_rows = provider.get_candles(symbol, timeframe=timeframe, limit=limit)
            environment = str(getattr(provider, "environment", "binance_testnet"))
        normalized: list[NormalizedCandle] = []
        for row in raw_rows:
            timestamp = row.get("timestamp_ms")
            if timestamp is None:
                # The MT5 bridge returns broker-server Unix seconds.
                timestamp = int(row.get("timestamp", 0)) * 1000
            candle = NormalizedCandle(
                venue=venue_name,
                environment=str(row.get("environment") or environment),
                symbol=str(row.get("symbol") or symbol),
                timeframe=str(row.get("timeframe") or timeframe),
                timestamp_ms=int(timestamp),
                open=_finite_number(row.get("open")),
                high=_finite_number(row.get("high")),
                low=_finite_number(row.get("low")),
                close=_finite_number(row.get("close")),
                volume=_finite_number(row.get("volume")),
            )
            if candle.timestamp_ms > 0 and min(candle.open, candle.high, candle.low, candle.close) > 0:
                normalized.append(candle)
        normalized.sort(key=lambda bar: bar.timestamp_ms)
        return [bar.to_dict() for bar in normalized[-max(1, min(1500, int(limit))):]]
