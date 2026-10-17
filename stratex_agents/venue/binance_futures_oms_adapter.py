"""Binance USDⓈ-M Futures venue submitter for Stratex's demo-only OMS.

The adapter intentionally supports MARKET entry intents only in this first slice.
It places both broker-native protective conditional orders before reporting success.
If full protection cannot be established, it attempts to close the demo position;
ambiguous venue outcomes are raised so the shared OMS records UNKNOWN and blocks
blind retries. This module does not enable production endpoints.
"""
from __future__ import annotations

import hashlib
import os
from decimal import Decimal, ROUND_DOWN
from typing import Any

from stratex_agents.order_intent import TradeIntent
from stratex_agents.venue.binance_futures_agent import BinanceFuturesAgent


class BinanceVenueError(RuntimeError):
    """The Binance demo adapter could not establish a safe order state."""


class BinanceProtectionError(BinanceVenueError):
    """Protective conditional-order setup failed after a stop was acknowledged."""

    def __init__(self, message: str, stop_response: dict[str, Any] | None = None):
        super().__init__(message)
        self.stop_response = stop_response


def _truthy(value: str | None) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes", "on"}


def _algo_client_id(client_order_id: str, suffix: str) -> str:
    digest = hashlib.sha256(client_order_id.encode("utf-8")).hexdigest()[:22]
    return f"stx{suffix}-{digest}"


class BinanceFuturesOMSAdapter:
    """Submit normalized Stratex intents through the selected demo/testnet Futures API."""

    def __init__(self, client: Any | None = None, venue_agent: BinanceFuturesAgent | None = None):
        if _truthy(os.getenv("LIVE_TRADING_ENABLED")):
            raise BinanceVenueError("LIVE_TRADING_ENABLED must remain false.")
        self.venue_agent = venue_agent or BinanceFuturesAgent()
        self.client = client or self.venue_agent.create_client(
            mode="FUTURES",
            legacy_api_key=os.getenv("API_KEY", ""),
            legacy_secret_key=os.getenv("SECRET_KEY", ""),
        )
        expected = "demo_trading" if self.venue_agent.demo_trading else "legacy_futures_testnet"
        actual = getattr(self.client, "stratex_environment", expected)
        if actual != expected:
            raise BinanceVenueError(f"Binance client environment mismatch: expected {expected!r}, got {actual!r}")
        if _truthy(os.getenv("LIVE_TRADING_ENABLED")):
            raise BinanceVenueError("Live-money trading is blocked.")

    def _position_mode(self) -> bool:
        """Return true for Hedge Mode. Fail closed rather than guessing the account mode."""
        try:
            mode = self.client.futures_get_position_mode()
        except Exception as exc:
            raise BinanceVenueError("Cannot verify Binance Futures position mode; no order was submitted.") from exc
        if not isinstance(mode, dict) or "dualSidePosition" not in mode:
            raise BinanceVenueError("Binance returned an invalid position-mode response; no order was submitted.")
        return str(mode["dualSidePosition"]).strip().lower() in {"true", "1"}

    def _market_rules(self, symbol: str) -> dict[str, Any]:
        try:
            info = self.client.futures_exchange_info()
        except Exception as exc:
            raise BinanceVenueError("Could not load Binance Futures contract rules.") from exc
        for market in info.get("symbols", []):
            if market.get("symbol") == symbol:
                if market.get("status") != "TRADING":
                    raise BinanceVenueError(f"Binance Futures symbol {symbol} is not trading.")
                filters = {item.get("filterType"): item for item in market.get("filters", [])}
                lot = filters.get("LOT_SIZE") or {}
                return {
                    "step_size": Decimal(str(lot.get("stepSize", "0"))),
                    "min_qty": Decimal(str(lot.get("minQty", "0"))),
                    "min_notional": Decimal(str(
                        (filters.get("MIN_NOTIONAL") or filters.get("NOTIONAL") or {}).get("notional", "0")
                    )),
                }
        raise BinanceVenueError(f"Symbol {symbol!r} is not listed in the selected Binance Futures environment.")

    @staticmethod
    def _round_quantity(quantity: float, step_size: Decimal) -> Decimal:
        value = Decimal(str(quantity))
        if step_size <= 0:
            return value
        return (value / step_size).to_integral_value(rounding=ROUND_DOWN) * step_size

    @staticmethod
    def _validate_bracket(is_long: bool, entry_price: float, stop_loss: float, take_profit: float) -> None:
        if entry_price <= 0:
            raise BinanceVenueError("Binance did not provide a valid filled entry price.")
        if is_long and not stop_loss < entry_price < take_profit:
            raise BinanceVenueError("For a long entry, stop_loss < filled entry price < take_profit is required.")
        if not is_long and not take_profit < entry_price < stop_loss:
            raise BinanceVenueError("For a short entry, take_profit < filled entry price < stop_loss is required.")

    def _place_protection(
        self, *, symbol: str, is_long: bool, position_side: str | None,
        stop_loss: float, take_profit: float, client_order_id: str,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        exit_side = "SELL" if is_long else "BUY"
        common: dict[str, Any] = {
            "algoType": "CONDITIONAL",
            "symbol": symbol,
            "side": exit_side,
            "closePosition": "true",
            "workingType": "MARK_PRICE",
            "priceProtect": "TRUE",
        }
        if position_side:
            common["positionSide"] = position_side

        stop = self.client.futures_create_algo_order(
            **common,
            type="STOP_MARKET",
            triggerPrice=str(stop_loss),
            clientAlgoId=_algo_client_id(client_order_id, "sl"),
        )
        try:
            take = self.client.futures_create_algo_order(
                **common,
                type="TAKE_PROFIT_MARKET",
                triggerPrice=str(take_profit),
                clientAlgoId=_algo_client_id(client_order_id, "tp"),
            )
        except Exception as exc:
            # The caller will close the exposure and preserve this order ID until
            # the close is confirmed, then cancel the stop if still open.
            raise BinanceProtectionError(
                "Take-profit protection failed after stop-loss was acknowledged.", stop_response=stop
            ) from exc
        return stop, take

    def _cancel_algo(self, symbol: str, response: dict[str, Any] | None) -> None:
        algo_id = (response or {}).get("algoId")
        if algo_id is not None:
            self.client.futures_cancel_algo_order(symbol=symbol, algoId=algo_id)

    def _safety_close(
        self, *, symbol: str, side: str, quantity: Decimal,
        hedge_mode: bool, position_side: str | None, client_order_id: str,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {
            "symbol": symbol, "side": "SELL" if side == "BUY" else "BUY",
            "type": "MARKET", "quantity": str(quantity),
            "newClientOrderId": _algo_client_id(client_order_id, "cx"),
            "newOrderRespType": "RESULT",
        }
        if hedge_mode:
            params["positionSide"] = position_side
        else:
            params["reduceOnly"] = "true"
        return self.client.futures_create_order(**params)

    def submit_intent(self, intent: TradeIntent) -> dict[str, Any]:
        if intent.venue != "binance":
            raise BinanceVenueError("Binance adapter received an intent for a different venue.")
        expected_environment = "binance_demo" if self.venue_agent.demo_trading else "binance_testnet"
        if intent.environment != expected_environment:
            raise BinanceVenueError(
                f"Intent environment {intent.environment!r} does not match Binance environment {expected_environment!r}."
            )
        if intent.order_type != "MARKET":
            raise BinanceVenueError("This Binance OMS adapter currently accepts MARKET entries only.")
        if not _truthy(os.getenv("LIVE_TRADING_ENABLED", "false")):
            pass
        else:
            raise BinanceVenueError("Live-money execution is disabled.")

        symbol = intent.symbol.replace("/", "")
        is_long = intent.side in {"BUY", "LONG"}
        side = "BUY" if is_long else "SELL"
        hedge_mode = self._position_mode()
        position_side = ("LONG" if is_long else "SHORT") if hedge_mode else None
        rules = self._market_rules(symbol)
        quantity = self._round_quantity(intent.quantity, rules["step_size"])
        if quantity <= 0 or quantity < rules["min_qty"]:
            raise BinanceVenueError("Order quantity rounds below Binance's minimum quantity.")

        try:
            ticker = self.client.futures_symbol_ticker(symbol=symbol)
            market_price = Decimal(str(ticker.get("price", "0")))
        except Exception as exc:
            raise BinanceVenueError("Unable to validate Binance Futures market price.") from exc
        if market_price <= 0:
            raise BinanceVenueError("Binance Futures market price is invalid.")
        if quantity * market_price < rules["min_notional"]:
            raise BinanceVenueError("Order notional is below Binance's minimum notional.")

        entry_params: dict[str, Any] = {
            "symbol": symbol, "side": side, "type": "MARKET",
            "quantity": str(quantity), "newClientOrderId": intent.client_order_id,
            "newOrderRespType": "RESULT",
        }
        if position_side:
            entry_params["positionSide"] = position_side

        # A transport exception can happen after the exchange accepted the order.
        # Propagate it to OMS, which stores UNKNOWN and refuses a blind retry.
        entry = self.client.futures_create_order(**entry_params)
        status = str(entry.get("status", "UNKNOWN")).upper()
        executed = Decimal(str(entry.get("executedQty", "0") or "0"))
        avg_price = float(entry.get("avgPrice", 0) or 0)

        if status != "FILLED" or executed <= 0 or avg_price <= 0:
            try:
                latest = self.client.futures_get_order(symbol=symbol, origClientOrderId=intent.client_order_id)
                status = str(latest.get("status", status)).upper()
                executed = Decimal(str(latest.get("executedQty", executed) or "0"))
                avg_price = float(latest.get("avgPrice", avg_price) or 0)
                if status in {"NEW", "PARTIALLY_FILLED"}:
                    self.client.futures_cancel_order(symbol=symbol, origClientOrderId=intent.client_order_id)
                    latest = self.client.futures_get_order(symbol=symbol, origClientOrderId=intent.client_order_id)
                    status = str(latest.get("status", status)).upper()
                    executed = Decimal(str(latest.get("executedQty", executed) or "0"))
                    avg_price = float(latest.get("avgPrice", avg_price) or 0)
            except Exception as exc:
                raise BinanceVenueError("Entry order outcome is ambiguous; reconcile before any retry.") from exc
            if executed <= 0 or avg_price <= 0:
                return {
                    "status": "REJECTED" if status in {"CANCELED", "CANCELLED", "EXPIRED", "REJECTED"} else "UNKNOWN",
                    "symbol": symbol, "side": side, "order_id": entry.get("orderId"),
                    "client_order_id": intent.client_order_id, "reason": f"entry status {status} has no confirmed fill",
                }

        stop_response: dict[str, Any] | None = None
        take_response: dict[str, Any] | None = None
        try:
            self._validate_bracket(is_long, avg_price, intent.stop_loss, intent.take_profit)
            stop_response, take_response = self._place_protection(
                symbol=symbol, is_long=is_long, position_side=position_side,
                stop_loss=intent.stop_loss, take_profit=intent.take_profit,
                client_order_id=intent.client_order_id,
            )
        except Exception as protection_error:
            # Preserve any acknowledged stop while attempting to close exposure;
            # cancel the stop only after the close is confirmed.
            if isinstance(protection_error, BinanceProtectionError):
                stop_response = protection_error.stop_response
            try:
                close = self._safety_close(
                    symbol=symbol, side=side, quantity=executed, hedge_mode=hedge_mode,
                    position_side=position_side, client_order_id=intent.client_order_id,
                )
                close_status = str(close.get("status", "UNKNOWN")).upper()
                if close_status != "FILLED":
                    raise BinanceVenueError("Safety close was not confirmed filled.")
                if stop_response:
                    self._cancel_algo(symbol, stop_response)
            except Exception as close_error:
                raise BinanceVenueError(
                    f"Protection setup failed ({type(protection_error).__name__}); emergency close is unconfirmed."
                ) from close_error
            return {
                "status": "REJECTED",
                "symbol": symbol, "side": side, "order_id": entry.get("orderId"),
                "client_order_id": intent.client_order_id,
                "reason": "Protection setup failed; demo exposure was closed as a safety abort.",
            }

        return {
            "status": "FILLED",
            "symbol": symbol, "side": side, "order_type": "MARKET",
            "order_id": str(entry.get("orderId", "")),
            "client_order_id": intent.client_order_id,
            "quantity": float(executed), "avg_price": avg_price,
            "position_side": position_side or "BOTH",
            "stop_loss": intent.stop_loss, "stop_algo_id": stop_response.get("algoId"),
            "take_profit": intent.take_profit, "take_profit_algo_id": take_response.get("algoId"),
            "venue": "binance", "environment": expected_environment,
        }
