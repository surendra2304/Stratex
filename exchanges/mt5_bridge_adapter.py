"""Stratex adapter for an authenticated, demo-only MetaTrader 5 bridge.

The bridge runs beside the MT5 terminal (normally on a Windows VPS). Stratex Core
calls it over a private network/VPN. This module deliberately has no direct
MetaTrader5 dependency and never falls back to fabricated prices or positions.
"""
from __future__ import annotations

import os
import time
import uuid
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

    def get_equity(self) -> dict[str, float]:
        """Return verified MT5 account equity in the broker account currency."""
        data = self._request("GET", "/v1/account")
        if self.demo_only and not data.get("demo", False):
            raise MT5BridgeError("Refusing non-demo MT5 account equity.")
        currency = str(data.get("currency", "")).strip().upper()
        try:
            equity = float(data["equity"])
        except (KeyError, TypeError, ValueError) as exc:
            raise MT5BridgeError("MT5 account equity is missing or invalid.") from exc
        if not currency or equity <= 0 or equity != equity or abs(equity) == float("inf"):
            raise MT5BridgeError("MT5 account equity/currency is invalid.")
        return {currency: equity}

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

    def calculate_position_size(
        self,
        symbol: str,
        side: str,
        stop_loss: float,
        take_profit: float,
    ) -> dict[str, Any]:
        """Ask the MT5 demo terminal to size a lot using broker-calculated PnL and margin."""
        result = self._request(
            "POST", "/v1/risk/size",
            json={
                "symbol": symbol,
                "side": side.upper(),
                "stop_loss": float(stop_loss),
                "take_profit": float(take_profit),
            },
        )
        if not isinstance(result, dict) or result.get("approved") is not True:
            raise MT5BridgeError("MT5 broker-aware risk sizing did not approve this intent.")
        recommended = result.get("recommended_volume")
        if not isinstance(recommended, (int, float)) or recommended <= 0:
            raise MT5BridgeError("MT5 risk sizing returned an invalid recommended volume.")
        return result

    def get_symbols(
        self,
        query: str = "",
        *,
        visible_only: bool = False,
        limit: int = 1000,
    ) -> list[dict[str, Any]]:
        """Discover broker-native symbols and actual contract rules from the MT5 terminal."""
        bounded_limit = max(1, min(5000, int(limit)))
        data = self._request(
            "GET", "/v1/symbols",
            params={"query": query, "visible_only": str(visible_only).lower(), "limit": bounded_limit},
        )
        if not isinstance(data, dict) or not isinstance(data.get("symbols"), list):
            raise MT5BridgeError("MT5 symbol discovery returned an invalid payload.")
        return data["symbols"]

    def get_symbol_info(self, symbol: str) -> dict[str, Any]:
        """Return exact symbol metadata; substring matches must not be mistaken for the requested symbol."""
        for item in self.get_symbols(query=symbol, visible_only=False, limit=5000):
            if str(item.get("symbol", "")).casefold() == symbol.casefold():
                return item
        raise MT5BridgeError(f"MT5 symbol {symbol!r} was not found in broker metadata.")

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

    def place_order(
        self,
        symbol: str,
        side: str,
        order_type: str,
        quantity: float,
        price: float | None = None,
        stop_price: float | None = None,
        *,
        stop_loss: float | None = None,
        take_profit: float | None = None,
        client_order_id: str | None = None,
    ) -> UnifiedOrderResult:
        if quantity <= 0:
            raise ValueError("quantity must be positive")
        normalized_side = side.upper()
        if normalized_side not in {"BUY", "SELL", "LONG", "SHORT"}:
            raise ValueError("side must be BUY/SELL/LONG/SHORT")
        kind = order_type.upper()
        if kind not in {"MARKET", "LIMIT", "STOP", "STOP_LIMIT"}:
            raise ValueError("unsupported MT5 order type")
        if stop_loss is None or take_profit is None:
            raise ValueError("MT5 entry orders require stop_loss and take_profit")
        data = self._request("POST", "/v1/orders", json={
            "symbol": symbol, "side": "BUY" if normalized_side in {"BUY", "LONG"} else "SELL",
            "type": kind, "volume": quantity, "price": price, "stop_price": stop_price,
            "stop_loss": stop_loss, "take_profit": take_profit,
            "client_order_id": client_order_id or f"stx-{uuid.uuid4().hex[:28]}",
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

    def submit_intent(self, intent) -> dict[str, Any]:
        """Submit a normalized Stratex TradeIntent with broker-native SL/TP protection."""
        if getattr(intent, "venue", None) != "mt5" or getattr(intent, "environment", None) != "mt5_demo":
            raise MT5BridgeError("MT5 adapter accepts only explicitly routed MT5 demo intents.")
        sizing = self.calculate_position_size(
            intent.symbol, intent.side, intent.stop_loss, intent.take_profit
        )
        recommended_volume = float(sizing["recommended_volume"])
        requested_volume = float(intent.quantity)
        if requested_volume > recommended_volume + 1e-9:
            raise MT5BridgeError(
                f"Requested MT5 volume {requested_volume} exceeds broker-aware safe volume {recommended_volume}."
            )
        result = self.place_order(
            symbol=intent.symbol,
            side=intent.side,
            order_type=intent.order_type,
            quantity=intent.quantity,
            price=getattr(intent, "limit_price", None),
            stop_loss=intent.stop_loss,
            take_profit=intent.take_profit,
            client_order_id=intent.client_order_id,
        )
        return {
            "status": result.status,
            "order_id": result.order_id,
            "symbol": result.symbol,
            "side": result.side,
            "order_type": result.order_type,
            "quantity": result.quantity,
            "executed_qty": result.executed_qty,
            "avg_price": result.avg_price,
            "exchange": result.exchange,
            "client_order_id": intent.client_order_id,
        }

    def reconcile_intent(self, stored: dict[str, Any]) -> dict[str, Any]:
        """Read the persistent bridge record; UNKNOWN is never auto-resolved by assumption."""
        payload = stored.get("payload") if isinstance(stored, dict) else None
        if not isinstance(payload, dict) or payload.get("venue") != "mt5":
            return {"confirmed": False, "status": "UNKNOWN", "evidence": "invalid_stored_intent"}
        if payload.get("environment") != "mt5_demo":
            return {"confirmed": False, "status": "UNKNOWN", "evidence": "environment_mismatch"}
        client_order_id = str(stored.get("client_order_id") or payload.get("client_order_id") or "")
        if not client_order_id:
            return {"confirmed": False, "status": "UNKNOWN", "evidence": "missing_client_order_id"}
        try:
            record = self.get_order_by_client_id(client_order_id)
        except MT5BridgeError:
            return {"confirmed": False, "status": "UNKNOWN", "evidence": "bridge_order_record_not_confirmed"}
        status = str(record.get("status", "UNKNOWN")).upper()
        if record.get("manual_reconciliation_required") is True or status == "UNKNOWN":
            return {
                "confirmed": False,
                "status": "UNKNOWN",
                "evidence": "MT5 bridge flags ambiguous order state for manual reconciliation",
            }
        accepted = {"ACCEPTED", "PARTIALLY_FILLED", "FILLED", "CANCELLED", "REJECTED"}
        if status not in accepted:
            return {"confirmed": False, "status": "UNKNOWN", "evidence": "unsupported_bridge_order_status"}
        response = record.get("response")
        if response is not None and not isinstance(response, dict):
            return {"confirmed": False, "status": "UNKNOWN", "evidence": "invalid_bridge_response"}
        return {
            "confirmed": True,
            "status": status,
            "result": response or {"status": status, "client_order_id": client_order_id, "venue": "mt5"},
            "evidence": "Persistent MT5 bridge record confirmed order state",
        }

    def get_order_by_client_id(self, client_order_id: str) -> dict[str, Any]:
        return self._request("GET", f"/v1/orders/by-client-id/{client_order_id}")

    def close_position(self, position_id: str) -> bool:
        """Close exactly one MT5 position by native ticket (required for hedge mode)."""
        data = self._request("POST", f"/v1/positions/{position_id}/close", json={})
        return bool(data.get("closed", False))

    def get_fees(self, symbol: str) -> tuple[float, float]:
        data = self._request("GET", "/v1/fees", params={"symbol": symbol})
        return float(data.get("maker", 0)), float(data.get("taker", 0))
