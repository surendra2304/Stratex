"""Stratex adapter for an authenticated, demo-only MetaTrader 5 bridge.

The bridge runs beside the MT5 terminal (normally on a Windows VPS). Stratex Core
calls it over a private network/VPN. This module deliberately has no direct
MetaTrader5 dependency and never falls back to fabricated prices or positions.
"""
from __future__ import annotations

import os
import time
from typing import Any

import requests

from exchanges.base_exchange import (
    BaseExchange, ExchangeCapabilities, UnifiedBalance, UnifiedOrderResult,
    UnifiedPosition, UnifiedTicker,
)


class MT5BridgeError(RuntimeError):
    """Raised when the MT5 bridge is unavailable or rejects a request."""


class MT5BridgeAdapter(BaseExchange):
    """Remote MT5 adapter. Disabled unless explicitly enabled and configured."""

    def __init__(
        self,
        base_url: str | None = None,
        token: str | None = None,
        timeout: float = 5.0,
        session: requests.Session | None = None,
    ):
        super().__init__(
            "mt5",
            capabilities=ExchangeCapabilities(
                supports_futures=True, supports_shorting=True, supports_margin=True,
                supports_limit_orders=True, supports_stop_orders=True,
                supports_websocket=False, max_leverage=100.0,
            ),
        )
        self.base_url = (base_url or os.getenv("MT5_BRIDGE_URL", "")).rstrip("/")
        self.token = token or os.getenv("MT5_BRIDGE_TOKEN", "")
        self.timeout = float(timeout)
        self.session = session or requests.Session()
        self.enabled = os.getenv("MT5_BRIDGE_ENABLED", "false").lower() in {"1", "true", "yes"}
        self.demo_only = os.getenv("MT5_REQUIRE_DEMO", "true").lower() not in {"0", "false", "no"}
        self.is_connected = False

    def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        if not self.enabled:
            raise MT5BridgeError("MT5 adapter is disabled; set MT5_BRIDGE_ENABLED=true to enable it.")
        if not self.base_url or not self.token:
            raise MT5BridgeError("MT5_BRIDGE_URL and MT5_BRIDGE_TOKEN must be configured.")
        headers = dict(kwargs.pop("headers", {}))
        headers["Authorization"] = f"Bearer {self.token}"
        try:
            response = self.session.request(
                method, f"{self.base_url}{path}", headers=headers,
                timeout=self.timeout, **kwargs,
            )
            response.raise_for_status()
            payload = response.json()
        except (requests.RequestException, ValueError) as exc:
            self.is_connected = False
            raise MT5BridgeError(f"MT5 bridge request failed: {type(exc).__name__}") from exc
        if not isinstance(payload, dict) or payload.get("ok") is not True:
            reason = payload.get("error", "invalid bridge response") if isinstance(payload, dict) else "invalid bridge response"
            raise MT5BridgeError(str(reason))
        self.is_connected = True
        return payload.get("data")

    def health(self) -> dict[str, Any]:
        data = self._request("GET", "/v1/health")
        if self.demo_only and not data.get("demo", False):
            raise MT5BridgeError("Refusing non-demo MT5 account; demo-only mode is enabled.")
        return data

    def get_balance(self) -> dict[str, UnifiedBalance]:
        data = self._request("GET", "/v1/account")
        if self.demo_only and not data.get("demo", False):
            raise MT5BridgeError("Refusing non-demo MT5 account.")
        currency = str(data.get("currency", "USD"))
        balance = float(data.get("balance", 0))
        equity = float(data.get("equity", balance))
        margin = float(data.get("margin", 0))
        return {currency: UnifiedBalance(currency, free=max(0.0, equity - margin), used=margin, total=equity)}

    def get_positions(self) -> list[UnifiedPosition]:
        data = self._request("GET", "/v1/positions")
        result = []
        for p in data:
            volume = float(p.get("volume", 0))
            if volume <= 0:
                continue
            result.append(UnifiedPosition(
                symbol=str(p["symbol"]), side="LONG" if int(p.get("type", 0)) == 0 else "SHORT",
                quantity=volume, entry_price=float(p.get("price_open", 0)),
                mark_price=float(p.get("price_current", 0)), unrealized_pnl=float(p.get("profit", 0)),
                leverage=float(p.get("leverage", 1)), exchange="mt5",
                position_id=str(p.get("ticket", "")) or None,
            ))
            # MT5 position tickets remain part of the raw response/bridge ledger; never
            # merge hedge-mode positions solely by symbol.
        return result

    def get_ticker(self, symbol: str) -> UnifiedTicker:
        data = self._request("GET", "/v1/ticker", params={"symbol": symbol})
        return UnifiedTicker(
            symbol=symbol, bid=float(data["bid"]), ask=float(data["ask"]),
            last=float(data.get("last", (float(data["bid"]) + float(data["ask"])) / 2)),
            volume_24h=float(data.get("volume", 0)), timestamp=time.time(),
        )

    def get_orderbook(self, symbol: str, limit: int = 20) -> dict[str, list[list[float]]]:
        data = self._request("GET", "/v1/orderbook", params={"symbol": symbol, "limit": limit})
        return {"bids": data.get("bids", []), "asks": data.get("asks", [])}

    def place_order(self, symbol: str, side: str, order_type: str, quantity: float,
                    price: float | None = None, stop_price: float | None = None) -> UnifiedOrderResult:
        if quantity <= 0:
            raise ValueError("quantity must be positive")
        normalized_side = side.upper()
        if normalized_side not in {"BUY", "SELL", "LONG", "SHORT"}:
            raise ValueError("side must be BUY/SELL/LONG/SHORT")
        kind = order_type.upper()
        if kind not in {"MARKET", "LIMIT", "STOP", "STOP_LIMIT"}:
            raise ValueError("unsupported MT5 order type")
        data = self._request("POST", "/v1/orders", json={
            "symbol": symbol, "side": "BUY" if normalized_side in {"BUY", "LONG"} else "SELL",
            "type": kind, "volume": quantity, "price": price, "stop_price": stop_price,
            "client_order_id": None,
        })
        return UnifiedOrderResult(
            order_id=str(data.get("order", data.get("deal", ""))), symbol=symbol,
            side=normalized_side, order_type=kind, price=float(data.get("price", price or 0)),
            quantity=quantity, status=str(data.get("status", "PENDING")).upper(),
            executed_qty=float(data.get("volume_filled", 0)), avg_price=float(data.get("price", 0)),
            fee_paid=float(data.get("commission", 0)), exchange="mt5",
        )

    def cancel_order(self, symbol: str, order_id: str) -> bool:
        data = self._request("DELETE", f"/v1/orders/{order_id}", params={"symbol": symbol})
        return bool(data.get("cancelled", False))

    def get_historical_data(self, symbol: str, timeframe: str = "15m", limit: int = 100) -> list[dict[str, Any]]:
        return self._request("GET", "/v1/candles", params={"symbol": symbol, "timeframe": timeframe, "limit": limit})

    def get_fees(self, symbol: str) -> tuple[float, float]:
        data = self._request("GET", "/v1/fees", params={"symbol": symbol})
        return float(data.get("maker", 0)), float(data.get("taker", 0))
