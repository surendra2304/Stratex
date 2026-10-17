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
from decimal import Decimal, ROUND_CEILING, ROUND_DOWN, ROUND_FLOOR
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
                        (filters.get("MIN_NOTIONAL") or filters.get("NOTIONAL") or {}).get(
                            "notional",
                            (filters.get("MIN_NOTIONAL") or filters.get("NOTIONAL") or {}).get("minNotional", "0"),
                        )
                    )),
                    "tick_size": Decimal(str((filters.get("PRICE_FILTER") or {}).get("tickSize", "0"))),
                }
        raise BinanceVenueError(f"Symbol {symbol!r} is not listed in the selected Binance Futures environment.")

    @staticmethod
    def _round_quantity(quantity: float, step_size: Decimal) -> Decimal:
        value = Decimal(str(quantity))
        if step_size <= 0:
            return value
        return (value / step_size).to_integral_value(rounding=ROUND_DOWN) * step_size

    @staticmethod
    def _round_price(value: float, tick_size: Decimal, rounding) -> Decimal:
        price = Decimal(str(value))
        if tick_size <= 0:
            return price
        return (price / tick_size).to_integral_value(rounding=rounding) * tick_size

    def _assert_symbol_flat(self, symbol: str) -> None:
        """Refuse entries when an existing exchange position needs reconciliation."""
        try:
            positions = self.client.futures_position_information(symbol=symbol)
        except Exception as exc:
            raise BinanceVenueError(
                f"Cannot reconcile current Futures positions for {symbol}; no new order was submitted."
            ) from exc
        if not isinstance(positions, list):
            raise BinanceVenueError(f"Binance returned an invalid position response for {symbol}.")
        for position in positions:
            try:
                amount = Decimal(str(position.get("positionAmt", "0") or "0"))
            except Exception as exc:
                raise BinanceVenueError(
                    f"Cannot verify an existing Futures position for {symbol}; refusing a new entry."
                ) from exc
            if amount != 0:
                side = str(position.get("positionSide", "BOTH")).upper()
                raise BinanceVenueError(
                    f"Existing {side} position for {symbol} must be reconciled before a new OMS entry."
                )

    def _configure_margin_and_leverage(self, symbol: str, intent: TradeIntent) -> int:
        raw_leverage = intent.metadata.get("leverage", os.getenv("FUTURES_LEVERAGE", "5"))
        try:
            leverage_float = float(raw_leverage)
        except (TypeError, ValueError) as exc:
            raise BinanceVenueError("Configured Futures leverage is invalid.") from exc
        if not leverage_float.is_integer() or not 1 <= leverage_float <= 125:
            raise BinanceVenueError("Futures leverage must be a whole number between 1 and 125.")
        leverage = int(leverage_float)
        margin_type = str(os.getenv("FUTURES_MARGIN_TYPE", "ISOLATED")).strip().upper()
        if margin_type != "ISOLATED":
            raise BinanceVenueError("The Stratex OMS adapter currently permits ISOLATED margin only.")
        try:
            self.client.futures_change_margin_type(symbol=symbol, marginType=margin_type)
        except Exception as exc:
            if "-4046" not in str(exc) and "No need to change margin type" not in str(exc):
                raise BinanceVenueError(f"Could not set ISOLATED margin for {symbol}.") from exc
        try:
            result = self.client.futures_change_leverage(symbol=symbol, leverage=leverage)
        except Exception as exc:
            raise BinanceVenueError(f"Could not set Futures leverage for {symbol}.") from exc
        if isinstance(result, dict) and int(result.get("leverage", leverage)) != leverage:
            raise BinanceVenueError(f"Binance did not confirm requested leverage {leverage} for {symbol}.")
        return leverage

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
        if _truthy(os.getenv("LIVE_TRADING_ENABLED", "false")):
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

        self._assert_symbol_flat(symbol)
        leverage = self._configure_margin_and_leverage(symbol, intent)
        try:
            account = self.client.futures_account()
            available_balance = Decimal(str(account.get("availableBalance", "0") or "0"))
        except Exception as exc:
            raise BinanceVenueError("Cannot verify available Futures margin; no order was submitted.") from exc
        required_margin = (quantity * market_price) / Decimal(leverage)
        if available_balance <= 0 or required_margin > available_balance:
            raise BinanceVenueError("Insufficient verified available Futures margin for this demo order.")

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
        rounded_stop = self._round_price(
            intent.stop_loss, rules["tick_size"], ROUND_CEILING if is_long else ROUND_FLOOR
        )
        rounded_take = self._round_price(
            intent.take_profit, rules["tick_size"], ROUND_FLOOR if is_long else ROUND_CEILING
        )
        try:
            self._validate_bracket(is_long, avg_price, float(rounded_stop), float(rounded_take))
            stop_response, take_response = self._place_protection(
                symbol=symbol, is_long=is_long, position_side=position_side,
                stop_loss=float(rounded_stop), take_profit=float(rounded_take),
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
            "stop_loss": float(rounded_stop), "stop_algo_id": stop_response.get("algoId"),
            "take_profit": float(rounded_take), "take_profit_algo_id": take_response.get("algoId"),
            "venue": "binance", "environment": expected_environment,
        }


    def reconcile_intent(self, stored: dict[str, Any]) -> dict[str, Any]:
        """Query entry and both protective Algo Orders; never mutate venue state."""
        payload = stored.get("payload") if isinstance(stored, dict) else None
        if not isinstance(payload, dict) or payload.get("venue") != "binance":
            return {"confirmed": False, "status": "UNKNOWN", "evidence": "invalid_stored_intent"}
        environment = "binance_demo" if self.venue_agent.demo_trading else "binance_testnet"
        if payload.get("environment") != environment:
            return {"confirmed": False, "status": "UNKNOWN", "evidence": "environment_mismatch"}

        symbol = str(payload.get("symbol", "")).replace("/", "").strip().upper()
        client_order_id = str(stored.get("client_order_id") or payload.get("client_order_id") or "")
        if not symbol or not client_order_id:
            return {"confirmed": False, "status": "UNKNOWN", "evidence": "missing_symbol_or_client_id"}

        try:
            entry = self.client.futures_get_order(symbol=symbol, origClientOrderId=client_order_id)
        except Exception:
            # "Not found" is not proof the entry never reached the exchange.
            return {"confirmed": False, "status": "UNKNOWN", "evidence": "entry_lookup_not_confirmed"}

        status = str(entry.get("status", "UNKNOWN")).strip().upper()
        if status in {"REJECTED", "CANCELED", "CANCELLED", "EXPIRED"}:
            normalized = "CANCELLED" if status in {"CANCELED", "CANCELLED", "EXPIRED"} else "REJECTED"
            return {
                "confirmed": True,
                "status": normalized,
                "result": {
                    "status": normalized, "symbol": symbol, "client_order_id": client_order_id,
                    "order_id": entry.get("orderId"), "venue": "binance",
                    "environment": environment, "venue_status": status,
                },
                "evidence": "Binance entry order returned a terminal canceled/rejected state",
            }
        if status in {"NEW", "PENDING_NEW"}:
            return {
                "confirmed": True,
                "status": "ACCEPTED",
                "result": {
                    "status": "ACCEPTED", "symbol": symbol, "client_order_id": client_order_id,
                    "order_id": entry.get("orderId"), "venue": "binance",
                    "environment": environment, "venue_status": status,
                },
                "evidence": "Binance confirmed the entry order is open",
            }
        if status != "FILLED":
            return {
                "confirmed": False,
                "status": "UNKNOWN",
                "evidence": f"entry status {status or 'missing'} needs deeper reconciliation",
            }

        try:
            executed = float(entry.get("executedQty", 0) or 0)
            avg_price = float(entry.get("avgPrice", 0) or 0)
        except (TypeError, ValueError):
            return {"confirmed": False, "status": "UNKNOWN", "evidence": "invalid_entry_fill_values"}
        if executed <= 0 or avg_price <= 0:
            return {"confirmed": False, "status": "UNKNOWN", "evidence": "missing_confirmed_entry_fill"}

        sl_client_id = _algo_client_id(client_order_id, "sl")
        tp_client_id = _algo_client_id(client_order_id, "tp")
        try:
            stop = self.client.futures_get_algo_order(symbol=symbol, clientAlgoId=sl_client_id)
            take = self.client.futures_get_algo_order(symbol=symbol, clientAlgoId=tp_client_id)
        except Exception:
            # Entry filled but one or both protection records cannot be verified.
            # Do not declare the trade fully protected.
            return {
                "confirmed": False,
                "status": "UNKNOWN",
                "evidence": "filled_entry_protection_state_not_confirmed",
            }

        stop_status = str(stop.get("algoStatus", stop.get("status", ""))).upper()
        take_status = str(take.get("algoStatus", take.get("status", ""))).upper()
        open_algo_states = {"NEW", "ACCEPTED", "WORKING"}
        if stop_status not in open_algo_states or take_status not in open_algo_states:
            return {
                "confirmed": False,
                "status": "UNKNOWN",
                "evidence": (
                    "filled entry but protective orders are not both confirmed working "
                    f"(stop={stop_status or 'missing'}, take={take_status or 'missing'})"
                ),
            }

        result = {
            "status": "FILLED",
            "symbol": symbol,
            "side": str(payload.get("side", "")).upper(),
            "order_type": str(payload.get("order_type", "MARKET")).upper(),
            "order_id": str(entry.get("orderId", "")),
            "client_order_id": client_order_id,
            "quantity": executed,
            "avg_price": avg_price,
            "position_side": str(stop.get("positionSide", "BOTH")).upper(),
            "stop_loss": float(payload.get("stop_loss", 0) or 0),
            "stop_algo_id": stop.get("algoId"),
            "take_profit": float(payload.get("take_profit", 0) or 0),
            "take_profit_algo_id": take.get("algoId"),
            "venue": "binance",
            "environment": environment,
            "protection_verified": True,
        }
        return {
            "confirmed": True,
            "status": "FILLED",
            "result": result,
            "evidence": "Binance confirmed entry fill and both protective Algo Orders working",
        }
