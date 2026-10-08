"""
testnet_engine/protection.py
----------------------------
TP/SL protection layer for Binance Spot Testnet.

API VERSION: python-binance 1.0.37
ENDPOINT: POST /api/v3/orderList/oco  (via client.create_oco_order)

The new OCO API (introduced ~2024) requires `aboveType` / `belowType`
instead of the old flat `price` / `stopPrice` / `stopLimitPrice` params.
Sending the old params causes:
  APIError -1102: Mandatory parameter 'aboveType' was not sent

=== NEW OCO PARAMETER MODEL ===

For a LONG position (entry was BUY, closing side is SELL):
  - Current price is between SL (below) and TP (above)
  - Above order = LIMIT_MAKER at TP price      → fires when price rises to TP
  - Below order = STOP_LOSS_LIMIT at SL price  → fires when price falls to SL

  aboveType      = "LIMIT_MAKER"
  abovePrice     = tp_price
  belowType      = "STOP_LOSS_LIMIT"
  belowStopPrice = sl_price
  belowPrice     = sl_price  (limit fill at same level; GTC)
  belowTimeInForce = "GTC"
  side           = "SELL"

For a SHORT position (entry was SELL, closing side is BUY):
  - Current price is between TP (below) and SL (above)
  - Above order = STOP_LOSS_LIMIT at SL price  → fires when price rises to SL
  - Below order = LIMIT_MAKER at TP price       → fires when price falls to TP

  aboveType      = "STOP_LOSS_LIMIT"
  aboveStopPrice = sl_price
  abovePrice     = sl_price
  aboveTimeInForce = "GTC"
  belowType      = "LIMIT_MAKER"
  belowPrice     = tp_price
  side           = "BUY"

Price constraint enforced by Binance:
  SELL OCO: abovePrice (TP) > last_price > belowStopPrice (SL)
  BUY OCO:  aboveStopPrice (SL) > last_price > belowPrice (TP)
"""

import json
import math
import os
import threading
from decimal import ROUND_FLOOR, Decimal, InvalidOperation

from binance.client import Client

from atomic_io import atomic_write_json
from logger import get_logger  # type: ignore[attr-defined]
from numeric_safety import finite_float, positive_float, require_positive, step_precision


logger = get_logger("protection")

# ---------------------------------------------------------------------------
# Atomic file write helper
# ---------------------------------------------------------------------------

def _atomic_write(path: str, data: list):
    """Write JSON list to path atomically (unique temp file + fsync + rename).

    The previous fixed ``<path>.tmp`` name let two concurrent writers clobber
    each other's temp file before the rename.
    """
    atomic_write_json(path, data)


# ---------------------------------------------------------------------------
# Symbol filter helpers
# ---------------------------------------------------------------------------

def _get_symbol_filters(client: Client, symbol: str) -> dict:
    """
    Fetch PRICE_FILTER and LOT_SIZE from exchange info.
    Returns {tick_size, price_precision, step_size, qty_precision, min_notional}.
    """
    info = client.get_symbol_info(symbol)
    if not info:
        raise ValueError(f"Symbol {symbol} not found on exchange.")

    return _parse_filters(info.get("filters", []), symbol, default_min_notional=10.0)


_DEFAULT_FILTERS = {"tick_size": 0.01, "price_precision": 2, "step_size": 0.001, "qty_precision": 3}


def _parse_filters(filters, symbol: str, *, default_min_notional: float) -> dict:
    """Parse PRICE_FILTER / LOT_SIZE / (MIN_)NOTIONAL into validated floats.

    A zero, negative, NaN or malformed tick/step size from the exchange used to
    flow into ``math.floor(price / tick)`` (ZeroDivisionError / ValueError) or
    disable rounding entirely; such filters are now rejected loudly.
    """
    result = dict(_DEFAULT_FILTERS, min_notional=default_min_notional)
    for f in filters or []:
        if not isinstance(f, dict):
            continue
        ft = f.get("filterType")
        if ft == "PRICE_FILTER":
            ts = positive_float(f.get("tickSize"))
            if ts is None:
                raise ValueError(f"Invalid PRICE_FILTER tickSize {f.get('tickSize')!r} for {symbol}")
            result["tick_size"] = ts
            result["price_precision"] = step_precision(ts)
        elif ft == "LOT_SIZE":
            ss = positive_float(f.get("stepSize"))
            if ss is None:
                raise ValueError(f"Invalid LOT_SIZE stepSize {f.get('stepSize')!r} for {symbol}")
            result["step_size"] = ss
            result["qty_precision"] = step_precision(ss)
        elif ft in ("MIN_NOTIONAL", "NOTIONAL"):
            mn = finite_float(f.get("minNotional", f.get("notional", default_min_notional)))
            if mn is None or mn < 0:
                raise ValueError(f"Invalid notional filter {f!r} for {symbol}")
            result["min_notional"] = mn

    return result


def _floor_decimal(value: float, step: float) -> Decimal:
    try:
        d_value = Decimal(repr(value))
        d_step = Decimal(repr(step))
        return (d_value / d_step).to_integral_value(rounding=ROUND_FLOOR) * d_step
    except (InvalidOperation, OverflowError) as exc:
        raise ValueError(f"cannot round {value!r} to step {step!r}") from exc


def round_price(price: float, tick_size: float, precision: int) -> str:
    """Round price DOWN to the nearest tick_size and return as formatted string.

    Raises ValueError for a non-finite/non-positive price or tick size: the old
    version formatted ``nan`` straight into the order (``"nan"``) when the tick
    size was 0, and raised OverflowError (not ValueError, so callers' emergency
    handling missed it) for ``inf``.
    """
    p = require_positive("price", price)
    tick = require_positive("tick_size", tick_size)
    rounded = _floor_decimal(p, tick)
    if rounded <= 0:
        raise ValueError(f"price {price!r} rounds to zero at tick {tick_size!r}")
    return f"{rounded:.{max(0, int(precision))}f}"


def round_qty(qty: float, step_size: float, precision: int) -> float:
    """Floor quantity to nearest step_size (0.0 when it rounds away).

    Raises ValueError for a non-finite quantity or an invalid step size; the old
    version returned the *unrounded* quantity for a zero step (rejected by the
    exchange after the entry had filled) and crashed on NaN.
    """
    q = finite_float(qty)
    if q is None:
        raise ValueError(f"quantity must be a finite number, got {qty!r}")
    step = require_positive("step_size", step_size)
    if q <= 0:
        return 0.0
    return round(float(_floor_decimal(q, step)), max(0, int(precision)))


def _validated_bracket_inputs(executed_qty, actual_fill_price, sl_price, tp_price):
    """All four bracket numbers must be finite and > 0.

    ``NaN`` compares False against everything, so the old ``sl >= fill`` style
    checks accepted NaN stops/targets and only failed later (or not at all).
    """
    qty = positive_float(executed_qty)
    if qty is None:
        raise ValueError(f"executed_qty must be positive, got {executed_qty}")
    fill = positive_float(actual_fill_price)
    if fill is None:
        raise ValueError(f"actual_fill_price must be positive, got {actual_fill_price}")
    sl = positive_float(sl_price)
    tp = positive_float(tp_price)
    if sl is None or tp is None:
        raise ValueError(f"sl_price/tp_price must be finite and positive, got sl={sl_price!r} tp={tp_price!r}")
    return qty, fill, sl, tp


# ---------------------------------------------------------------------------
# Core protection function
# ---------------------------------------------------------------------------

def place_oco_protection(
    client: Client,
    symbol: str,
    entry_side: str,           # "BUY" or "SELL"
    executed_qty: float,
    actual_fill_price: float,
    sl_price: float,
    tp_price: float,
    list_client_order_id: str | None = None,
) -> dict:
    """
    Place an OCO order protecting an open position.

    Uses python-binance 1.0.37 create_oco_order() which posts to
    POST /api/v3/orderList/oco with aboveType/belowType parameters.

    Args:
        client:               Authenticated Binance client (testnet=True for Testnet)
        symbol:               e.g. "BTCUSDT"
        entry_side:           "BUY" (long) or "SELL" (short)
        executed_qty:         Actual filled quantity from entry order
        actual_fill_price:    Actual average fill price from entry fills
        sl_price:             Stop-loss price (strategy-specified, absolute)
        tp_price:             Take-profit price (strategy-specified, absolute)
        list_client_order_id: Optional idempotency ID for the OCO list

    Returns:
        dict with keys:
            oco_order_list_id   — Binance orderListId (int)
            tp_order_id         — orderId of the TP leg
            sl_order_id         — orderId of the SL leg
            tp_client_order_id  — clientOrderId of the TP leg
            sl_client_order_id  — clientOrderId of the SL leg
            tp_price_sent       — formatted price string sent to API
            sl_price_sent       — formatted price string sent to API
            qty_sent            — formatted quantity string sent to API

    Raises:
        BinanceAPIException   — on API rejection (caller must emergency-close)
        ValueError            — on invalid inputs (caller must emergency-close)
    """
    # ------------------------------------------------------------------
    # 1. Validate inputs
    # ------------------------------------------------------------------
    executed_qty, actual_fill_price, sl_price, tp_price = _validated_bracket_inputs(
        executed_qty, actual_fill_price, sl_price, tp_price
    )
    if entry_side == "BUY":
        if sl_price >= actual_fill_price:
            raise ValueError(
                f"BUY position: SL ({sl_price}) must be below fill price ({actual_fill_price})"
            )
        if tp_price <= actual_fill_price:
            raise ValueError(
                f"BUY position: TP ({tp_price}) must be above fill price ({actual_fill_price})"
            )
    elif entry_side == "SELL":
        if sl_price <= actual_fill_price:
            raise ValueError(
                f"SELL position: SL ({sl_price}) must be above fill price ({actual_fill_price})"
            )
        if tp_price >= actual_fill_price:
            raise ValueError(
                f"SELL position: TP ({tp_price}) must be below fill price ({actual_fill_price})"
            )
    else:
        raise ValueError(f"entry_side must be 'BUY' or 'SELL', got {entry_side!r}")

    # ------------------------------------------------------------------
    # 2. Fetch exchange filters
    # ------------------------------------------------------------------
    filters = _get_symbol_filters(client, symbol)
    tick  = filters["tick_size"]
    pp    = filters["price_precision"]
    step  = filters["step_size"]
    qp    = filters["qty_precision"]
    min_notional = filters["min_notional"]

    # ------------------------------------------------------------------
    # 3. Round prices and quantity
    # ------------------------------------------------------------------
    qty_rounded = round_qty(executed_qty, step, qp)
    if qty_rounded <= 0:
        raise ValueError(f"After LOT_SIZE rounding, qty={qty_rounded} is 0 for {symbol}")
    if qty_rounded * actual_fill_price < min_notional:
        raise ValueError(
            f"Notional {qty_rounded * actual_fill_price:.2f} < MIN_NOTIONAL {min_notional} for {symbol}"
        )

    tp_str = round_price(tp_price, tick, pp)
    sl_str = round_price(sl_price, tick, pp)
    qty_str = f"{qty_rounded:.{qp}f}"

    logger.info(
        f"[PROTECTION] {symbol} {entry_side} | "
        f"Fill: {actual_fill_price:.{pp}f} | "
        f"TP: {tp_str} | SL: {sl_str} | Qty: {qty_str}"
    )

    # ------------------------------------------------------------------
    # 4. Build OCO params per the new aboveType/belowType API
    # ------------------------------------------------------------------
    oco_params = {
        "symbol":   symbol,
        "quantity": qty_str,
    }
    if list_client_order_id:
        oco_params["listClientOrderId"] = list_client_order_id

    if entry_side == "BUY":
        # Closing a LONG: OCO side = SELL
        # Above (higher price) = TP as LIMIT_MAKER
        # Below (lower price)  = SL as STOP_LOSS_LIMIT
        oco_params.update({
            "side":              "SELL",
            "aboveType":         "LIMIT_MAKER",
            "abovePrice":        tp_str,
            "belowType":         "STOP_LOSS_LIMIT",
            "belowStopPrice":    sl_str,
            "belowPrice":        sl_str,
            "belowTimeInForce":  "GTC",
        })
    else:
        # Closing a SHORT: OCO side = BUY
        # Above (higher price) = SL as STOP_LOSS_LIMIT
        # Below (lower price)  = TP as LIMIT_MAKER
        oco_params.update({
            "side":              "BUY",
            "aboveType":         "STOP_LOSS_LIMIT",
            "aboveStopPrice":    sl_str,
            "abovePrice":        sl_str,
            "aboveTimeInForce":  "GTC",
            "belowType":         "LIMIT_MAKER",
            "belowPrice":        tp_str,
        })

    # ------------------------------------------------------------------
    # 5. Place OCO
    # ------------------------------------------------------------------
    logger.info(f"[PROTECTION] Placing OCO with params: {oco_params}")
    oco_response = client.create_oco_order(**oco_params)

    order_list_id = oco_response.get("orderListId")

    # Parse TP and SL order IDs from orderReports
    tp_order_id = sl_order_id = None
    tp_client_id = sl_client_id = None

    for report in oco_response.get("orderReports", []):
        otype = report.get("type", "")
        oid   = report.get("orderId")
        cid   = report.get("clientOrderId")
        if otype == "LIMIT_MAKER":
            tp_order_id   = oid
            tp_client_id  = cid
        elif otype in ("STOP_LOSS_LIMIT", "STOP_LOSS"):
            sl_order_id   = oid
            sl_client_id  = cid

    if order_list_id is None:
        # Without a list id the protection can never be monitored or
        # cancelled; the caller treats this like any other protection failure.
        raise ValueError(f"OCO response for {symbol} carried no orderListId: {oco_response!r}")
    if tp_order_id is None or sl_order_id is None:
        logger.warning(
            f"[PROTECTION] OCO {order_list_id} for {symbol} response lacks leg ids "
            f"(tp={tp_order_id}, sl={sl_order_id}); monitoring by list id only."
        )

    logger.info(
        f"[PROTECTION] ✅ OCO placed. ListId={order_list_id} "
        f"TP_orderId={tp_order_id} SL_orderId={sl_order_id}"
    )

    return {
        "oco_order_list_id":  order_list_id,
        "tp_order_id":        tp_order_id,
        "sl_order_id":        sl_order_id,
        "tp_client_order_id": tp_client_id,
        "sl_client_order_id": sl_client_id,
        "tp_price_sent":      tp_str,
        "sl_price_sent":      sl_str,
        "qty_sent":           qty_str,
    }


# ---------------------------------------------------------------------------
# Emergency close
# ---------------------------------------------------------------------------

def emergency_market_close(
    client: Client,
    symbol: str,
    entry_side: str,
    executed_qty: float,
) -> dict:
    """
    Place an immediate MARKET order to close an unprotected position.

    Returns the Binance order response, or raises on failure.
    NEVER silently swallows errors — caller must decide what to do.
    """
    close_side = Client.SIDE_SELL if entry_side == "BUY" else Client.SIDE_BUY
    logger.critical(
        f"[PROTECTION] 🚨 EMERGENCY CLOSE: {symbol} {close_side} {executed_qty}"
    )
    response = client.create_order(
        symbol=symbol,
        side=close_side,
        type=Client.ORDER_TYPE_MARKET,
        quantity=executed_qty,
    )
    exec_qty = float(response.get("executedQty", 0))
    residual = max(0.0, executed_qty - exec_qty)
    response["_residual_qty"] = residual
    response["_is_flat"] = residual < 1e-8
    if not response["_is_flat"]:
        logger.critical(
            f"[PROTECTION] 🚨 EMERGENCY CLOSE PARTIAL: sent {executed_qty}, "
            f"filled {exec_qty}. Residual {residual} may remain open! State must be UNKNOWN."
        )
    else:
        logger.info(f"[PROTECTION] Emergency close FILLED: {exec_qty} @ market")
    return response



# ---------------------------------------------------------------------------
# OCO status monitoring  (replaces monitor_open_trades in execution.py)
# ---------------------------------------------------------------------------

LEDGER_WRITE_LOCK = threading.Lock()


def check_oco_status(client: Client, symbol: str, oco_order_list_id: int) -> dict:
    """
    Query the status of an OCO order list.

    Returns dict with keys:
        list_status      — "EXECUTING" | "ALL_DONE" | "REJECT" | "CANCELED" | "EXPIRED"
        tp_filled        — bool
        sl_filled        — bool
        close_avg_price  — float (actual fill price from cummulativeQuoteQty / executedQty)
        close_qty        — float
        tp_order_id      — int
        sl_order_id      — int
    """
    oco = client.v3_get_order_list(orderListId=oco_order_list_id)
    list_status = oco.get("listOrderStatus", "UNKNOWN")

    result = {
        "list_status":     list_status,
        "tp_filled":       False,
        "sl_filled":       False,
        "close_avg_price": 0.0,
        "close_qty":       0.0,
        "tp_order_id":     None,
        "sl_order_id":     None,
    }

    if list_status not in ("ALL_DONE", "DONE"):
        return result

    for order_ref in oco.get("orders", []):
        order_id = order_ref["orderId"]
        details  = client.get_order(symbol=symbol, orderId=order_id)
        otype    = details.get("type", "")
        status   = details.get("status", "")

        if status != "FILLED":
            continue

        exec_qty  = positive_float(details.get("executedQty"))
        cum_quote = positive_float(details.get("cummulativeQuoteQty"))
        if exec_qty is None or cum_quote is None:
            # A 0/NaN fill used to become close_avg_price=0.0, i.e. a
            # fabricated 100% loss in the ledger.
            raise ValueError(f"FILLED OCO leg {order_id} on {symbol} has unreadable fill data: {details!r}")

        result["close_avg_price"] = cum_quote / exec_qty
        result["close_qty"]       = exec_qty

        if otype == "LIMIT_MAKER":
            result["tp_filled"]    = True
            result["tp_order_id"]  = order_id
        elif otype in ("STOP_LOSS_LIMIT", "STOP_LOSS"):
            result["sl_filled"]    = True
            result["sl_order_id"]  = order_id

    if not (result["tp_filled"] or result["sl_filled"]):
        raise ValueError(
            f"OCO {oco_order_list_id} on {symbol} is {list_status} but no leg reports FILLED; "
            "refusing to book a close without a fill"
        )
    return result


def _get_futures_symbol_filters(client: Client, symbol: str) -> dict:
    """
    Fetch PRICE_FILTER and LOT_SIZE from Futures exchange info.
    Returns {tick_size, price_precision, step_size, qty_precision, min_notional}.
    """
    info = client.futures_exchange_info() if hasattr(client, 'futures_exchange_info') else None
    if not info:
        return {"tick_size": 0.01, "price_precision": 2, "step_size": 0.001, "qty_precision": 3, "min_notional": 5.0}

    symbol_info = next((s for s in info.get("symbols", []) if s.get("symbol") == symbol), None)
    if not symbol_info:
        return {"tick_size": 0.01, "price_precision": 2, "step_size": 0.001, "qty_precision": 3, "min_notional": 5.0}

    return _parse_filters(symbol_info.get("filters", []), symbol, default_min_notional=5.0)


def place_futures_bracket_protection(
    client: Client,
    symbol: str,
    entry_side: str,           # "BUY" (Long) or "SELL" (Short)
    executed_qty: float,
    actual_fill_price: float,
    sl_price: float,
    tp_price: float,
) -> dict:
    """
    Places conditional STOP_MARKET and TAKE_PROFIT_MARKET orders to protect a Futures position.
    
    Args:
        client: Authenticated Binance client
        symbol: e.g. "BTCUSDT"
        entry_side: "BUY" (closing side will be "SELL") or "SELL" (closing side will be "BUY")
        executed_qty: Position size
        actual_fill_price: Entry price
        sl_price: Stop loss price
        tp_price: Take profit price
        
    Returns:
        dict with keys: tp_order_id, sl_order_id, tp_price_sent, sl_price_sent, close_side
    """
    executed_qty, actual_fill_price, sl_price, tp_price = _validated_bracket_inputs(
        executed_qty, actual_fill_price, sl_price, tp_price
    )
    if entry_side not in ("BUY", "LONG", "SELL", "SHORT"):
        raise ValueError(f"entry_side must be BUY/LONG or SELL/SHORT, got {entry_side!r}")

    is_buy = entry_side in ("BUY", "LONG")
    close_side = "SELL" if is_buy else "BUY"

    if is_buy:
        if sl_price >= actual_fill_price:
            raise ValueError(f"BUY position: SL ({sl_price}) must be below fill price ({actual_fill_price})")
        if tp_price <= actual_fill_price:
            raise ValueError(f"BUY position: TP ({tp_price}) must be above fill price ({actual_fill_price})")
    else:
        if sl_price <= actual_fill_price:
            raise ValueError(f"SELL/SHORT position: SL ({sl_price}) must be above fill price ({actual_fill_price})")
        if tp_price >= actual_fill_price:
            raise ValueError(f"SELL/SHORT position: TP ({tp_price}) must be below fill price ({actual_fill_price})")

    filters = _get_futures_symbol_filters(client, symbol)
    tick = filters["tick_size"]
    pp = filters["price_precision"]

    tp_str = round_price(tp_price, tick, pp)
    sl_str = round_price(sl_price, tick, pp)

    logger.info(
        f"[FUTURES_PROTECTION] {symbol} {entry_side} | "
        f"Fill: {actual_fill_price:.{pp}f} | TP: {tp_str} | SL: {sl_str} | CloseSide: {close_side}"
    )

    # 1. Place Stop Loss conditional order (STOP_MARKET with closePosition=True)
    sl_order = client.futures_create_order(
        symbol=symbol,
        side=close_side,
        type="STOP_MARKET",
        stopPrice=sl_str,
        closePosition=True
    )
    sl_order_id = sl_order.get("orderId") or sl_order.get("algoId")
    if sl_order_id is None:
        raise ValueError(f"Futures SL response for {symbol} carried no order/algo id: {sl_order!r}")

    # 2. Place Take Profit conditional order (TAKE_PROFIT_MARKET with closePosition=True)
    try:
        tp_order = client.futures_create_order(
            symbol=symbol,
            side=close_side,
            type="TAKE_PROFIT_MARKET",
            stopPrice=tp_str,
            closePosition=True
        )
        tp_order_id = tp_order.get("orderId") or tp_order.get("algoId")
        if tp_order_id is None:
            raise ValueError(f"Futures TP response for {symbol} carried no order/algo id: {tp_order!r}")
    except Exception:
        # The caller will emergency-close the position. A surviving
        # closePosition STOP_MARKET would later close an unrelated *new*
        # position on this symbol, so cancel it before propagating.
        _cancel_futures_conditional(client, symbol, sl_order_id)
        raise

    return {
        "tp_order_id": tp_order_id,
        "sl_order_id": sl_order_id,
        "tp_price_sent": tp_str,
        "sl_price_sent": sl_str,
        "close_side": close_side,
    }


def _cancel_futures_conditional(client: Client, symbol: str, order_id) -> bool:
    """Best-effort cancel of a conditional (algo or regular) futures order."""
    for method, kwargs in (("futures_cancel_algo_order", {"algoId": order_id}),
                           ("futures_cancel_order", {"symbol": symbol, "orderId": order_id})):
        fn = getattr(client, method, None)
        if fn is None:
            continue
        try:
            fn(**kwargs)
            logger.warning(f"[FUTURES_PROTECTION] Cancelled orphan conditional order {order_id} on {symbol}")
            return True
        except Exception as exc:  # try the next endpoint
            logger.error(f"[FUTURES_PROTECTION] {method}({order_id}) failed: {exc}")
    logger.critical(f"[FUTURES_PROTECTION] 🚨 Could not cancel conditional order {order_id} on {symbol}; cancel it manually")
    return False


def emergency_futures_market_close(
    client: Client,
    symbol: str,
    entry_side: str,
    executed_qty: float,
) -> dict:
    """
    Emergency market order to immediately close a Futures position.
    """
    close_side = "SELL" if entry_side == "BUY" else "BUY"
    actual_qty = executed_qty
    try:
        if hasattr(client, "futures_position_information"):
            pos_info = client.futures_position_information(symbol=symbol, recvWindow=60000)
            for p in pos_info:
                amt = abs(float(p.get("positionAmt", 0.0)))
                if amt > 0:
                    actual_qty = max(actual_qty, amt)
                    break
    except Exception:
        pass

    logger.critical(
        f"[FUTURES_PROTECTION] 🚨 EMERGENCY CLOSE: {symbol} {close_side} {actual_qty}"
    )
    return client.futures_create_order(
        symbol=symbol,
        side=close_side,
        type="MARKET",
        quantity=actual_qty,
        reduceOnly=True,
        recvWindow=60000,
    )


def _futures_position_is_flat(client: Client, symbol: str) -> bool:
    """True only when the venue positively reports zero size for ``symbol``."""
    try:
        positions = client.futures_position_information(symbol=symbol)
    except Exception as exc:
        logger.warning(f"[FUTURES] Position query failed for {symbol}; treating as still open: {exc}")
        return False
    if not isinstance(positions, list) or not positions:
        logger.warning(f"[FUTURES] Empty/invalid position response for {symbol}; treating as still open")
        return False
    for position in positions:
        amount = finite_float(position.get("positionAmt")) if isinstance(position, dict) else None
        if amount is None:
            logger.warning(f"[FUTURES] Unreadable positionAmt for {symbol}; treating as still open")
            return False
        if amount != 0.0:
            return False
    return True


def check_futures_bracket_status(client: Client, symbol: str, tp_order_id: int | None, sl_order_id: int | None) -> dict:
    """
    Checks if either the TP or SL conditional order has fired and closed the futures position.
    Queries both order/algo endpoints and verifies against live Binance Futures position.
    """
    result = {
        "position_closed": False,
        "tp_filled": False,
        "sl_filled": False,
        "close_avg_price": 0.0,
        "close_qty": 0.0,
    }

    # Only a successful position query showing zero size on every entry proves
    # the position is closed. The old version treated a *failed* algo-order
    # query as "an algo order fired" and then booked the most recent account
    # trade (often the entry fill itself) as the close, marking an open
    # position closed at its entry price. One flat hedge-mode leg also counted
    # as "closed" while the other leg was still open.
    if _futures_position_is_flat(client, symbol):
        try:
            trades = client.futures_account_trades(symbol=symbol) if hasattr(client, "futures_account_trades") else []
            if trades:
                last_trade = trades[-1]
                close_price = positive_float(last_trade.get("price"))
                close_qty = positive_float(last_trade.get("qty"))
                pnl = finite_float(last_trade.get("realizedPnl", 0.0))
                if close_price is None or close_qty is None or pnl is None:
                    # Booking a 0/NaN close price would fabricate a huge loss
                    # (or gain); leave the trade open for reconciliation.
                    logger.error(f"[FUTURES] Unreadable closing trade for {symbol}: {last_trade!r}")
                    return result
                result["position_closed"] = True
                result["close_avg_price"] = close_price
                result["close_qty"] = close_qty
                if pnl > 0:
                    result["tp_filled"] = True
                else:
                    result["sl_filled"] = True

                # Cancel remaining opposing algo order
                other_id = sl_order_id if result["tp_filled"] else tp_order_id
                if other_id and hasattr(client, "futures_cancel_algo_order"):
                    try:
                        client.futures_cancel_algo_order(algoId=other_id)
                    except Exception:
                        pass
                return result
        except Exception as te:
            logger.debug(f"[FUTURES] Query trade history failed: {te}")

    return result


def compute_net_pnl(
    entry_side: str,
    entry_qty: float,
    entry_price: float,
    entry_fee: float,
    close_qty: float,
    close_price: float,
    close_fee: float,
) -> tuple:
    """
    Returns (gross_pnl, net_pnl) in quote currency (USDT).

    gross_pnl excludes all fees.
    net_pnl   subtracts entry_fee + close_fee.
    """
    for name, value in (("entry_qty", entry_qty), ("entry_price", entry_price),
                        ("close_qty", close_qty), ("close_price", close_price)):
        if positive_float(value) is None:
            raise ValueError(f"compute_net_pnl: {name}={value!r} must be a finite number > 0")
    for name, value in (("entry_fee", entry_fee), ("close_fee", close_fee)):
        fee = finite_float(value)
        if fee is None or fee < 0:
            raise ValueError(f"compute_net_pnl: {name}={value!r} must be a finite number >= 0")
    if entry_side not in ("BUY", "SELL"):
        raise ValueError(f"compute_net_pnl: entry_side must be BUY or SELL, got {entry_side!r}")
    match_qty = min(entry_qty, close_qty)
    if entry_side == "BUY":
        gross = (close_price - entry_price) * match_qty
    else:
        gross = (entry_price - close_price) * match_qty

    net = gross - entry_fee - close_fee
    return gross, net
