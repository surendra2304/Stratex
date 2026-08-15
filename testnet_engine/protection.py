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

import math
import json
import os
import datetime
import threading

from binance.client import Client
from binance.exceptions import BinanceAPIException

from logger import get_logger

logger = get_logger("protection")

# ---------------------------------------------------------------------------
# Atomic file write helper
# ---------------------------------------------------------------------------

def _atomic_write(path: str, data: list):
    """Write JSON list to path atomically via tmp file."""
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(data, f)
    os.replace(tmp, path)


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

    result = {"tick_size": 0.01, "price_precision": 2,
               "step_size": 0.001, "qty_precision": 3, "min_notional": 10.0}

    for f in info.get("filters", []):
        ft = f["filterType"]
        if ft == "PRICE_FILTER":
            ts = float(f["tickSize"])
            result["tick_size"] = ts
            result["price_precision"] = max(0, int(round(-math.log10(ts)))) if ts > 0 else 2
        elif ft == "LOT_SIZE":
            ss = float(f["stepSize"])
            result["step_size"] = ss
            result["qty_precision"] = max(0, int(round(-math.log10(ss)))) if ss > 0 else 3
        elif ft in ("MIN_NOTIONAL", "NOTIONAL"):
            result["min_notional"] = float(f.get("minNotional", f.get("notional", 10.0)))

    return result


def round_price(price: float, tick_size: float, precision: int) -> str:
    """Round price DOWN to the nearest tick_size and return as formatted string."""
    if tick_size <= 0:
        return f"{price:.{precision}f}"
    rounded = math.floor(price / tick_size) * tick_size
    return f"{rounded:.{precision}f}"


def round_qty(qty: float, step_size: float, precision: int) -> float:
    """Floor quantity to nearest step_size."""
    if step_size <= 0:
        return qty
    floored = math.floor(qty / step_size) * step_size
    return round(floored, precision)


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
    list_client_order_id: str = None,
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
    if executed_qty <= 0:
        raise ValueError(f"executed_qty must be positive, got {executed_qty}")
    if actual_fill_price <= 0:
        raise ValueError(f"actual_fill_price must be positive, got {actual_fill_price}")
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
    if exec_qty < executed_qty * 0.99:
        logger.critical(
            f"[PROTECTION] 🚨 EMERGENCY CLOSE PARTIAL: sent {executed_qty}, "
            f"filled {exec_qty}. Residual may remain open!"
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

        exec_qty     = float(details.get("executedQty", 0))
        cum_quote    = float(details.get("cummulativeQuoteQty", 0))
        avg_fill     = cum_quote / exec_qty if exec_qty > 0 else 0.0

        result["close_avg_price"] = avg_fill
        result["close_qty"]       = exec_qty

        if otype == "LIMIT_MAKER":
            result["tp_filled"]    = True
            result["tp_order_id"]  = order_id
        elif otype in ("STOP_LOSS_LIMIT", "STOP_LOSS"):
            result["sl_filled"]    = True
            result["sl_order_id"]  = order_id

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
    match_qty = min(entry_qty, close_qty)
    if entry_side == "BUY":
        gross = (close_price - entry_price) * match_qty
    else:
        gross = (entry_price - close_price) * match_qty

    net = gross - entry_fee - close_fee
    return gross, net
