"""Demo-only Flask bridge running beside a local MetaTrader 5 terminal.

This module is intended for a Windows VPS. It intentionally refuses live accounts.
Expose only over a private VPN/firewall and use a high-entropy bearer token.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from functools import wraps
from typing import Any

from flask import Flask, jsonify, request

from mt5_bridge.idempotency import (
    BridgeOrderStore, DuplicateOrderIdConflict, UnresolvedBridgeOrder,
)

app = Flask(__name__)
TOKEN = os.getenv("MT5_BRIDGE_TOKEN", "")
EXPECTED_SERVER = os.getenv("MT5_EXPECTED_SERVER", "ICMarketsSC-Demo")
BIND_HOST = os.getenv("MT5_BRIDGE_BIND", "127.0.0.1")
PORT = int(os.getenv("MT5_BRIDGE_PORT", "8765"))


_ORDER_STORES: dict[str, BridgeOrderStore] = {}


def _order_store() -> BridgeOrderStore:
    """Lazily open the bridge ledger so importing the Flask app creates no state."""
    path = os.getenv("MT5_BRIDGE_IDEMPOTENCY_DB", "mt5_bridge_orders.sqlite3")
    if path not in _ORDER_STORES:
        _ORDER_STORES[path] = BridgeOrderStore(path)
    return _ORDER_STORES[path]



def _mt5():
    try:
        import MetaTrader5 as mt5
    except ImportError as exc:
        raise RuntimeError("MetaTrader5 package is available only in the Windows MT5 bridge environment") from exc
    if not mt5.initialize():
        raise RuntimeError(f"MT5 initialize failed: {mt5.last_error()}")
    account = mt5.account_info()
    if account is None:
        raise RuntimeError("MT5 terminal has no authenticated account")
    server = str(getattr(account, "server", ""))
    trade_mode = int(getattr(account, "trade_mode", -1))
    demo_mode = getattr(mt5, "ACCOUNT_TRADE_MODE_DEMO", 0)
    if "demo" not in server.lower() or (EXPECTED_SERVER and EXPECTED_SERVER.lower() not in server.lower()):
        raise RuntimeError("Refusing MT5 account: server is not the configured demo server")
    if trade_mode != demo_mode:
        raise RuntimeError("Refusing MT5 account: account trade mode is not DEMO")
    return mt5, account


def _auth(fn):
    @wraps(fn)
    def wrapped(*args, **kwargs):
        if not TOKEN:
            return jsonify(ok=False, error="bridge token is not configured"), 503
        if request.headers.get("Authorization", "") != f"Bearer {TOKEN}":
            return jsonify(ok=False, error="unauthorized"), 401
        try:
            return fn(*args, **kwargs)
        except (ValueError, KeyError) as exc:
            return jsonify(ok=False, error=str(exc)), 400
        except RuntimeError as exc:
            return jsonify(ok=False, error=str(exc)), 503
    return wrapped


def _data(value: Any):
    return jsonify(ok=True, data=value)


@app.get("/v1/health")
@_auth
def health():
    mt5, account = _mt5()
    return _data({"demo": True, "server": str(account.server), "login": int(account.login),
                  "currency": str(account.currency), "terminal_connected": bool(mt5.terminal_info())})


@app.get("/v1/account")
@_auth
def account():
    _, a = _mt5()
    return _data({"demo": True, "server": str(a.server), "currency": str(a.currency),
                  "balance": float(a.balance), "equity": float(a.equity),
                  "margin": float(a.margin), "free_margin": float(a.margin_free)})


@app.get("/v1/positions")
@_auth
def positions():
    mt5, _ = _mt5()
    rows = mt5.positions_get()
    if rows is None:
        raise RuntimeError(f"positions_get failed: {mt5.last_error()}")
    return _data([{"ticket": int(p.ticket), "symbol": p.symbol, "type": int(p.type),
                   "volume": float(p.volume), "price_open": float(p.price_open),
                   "price_current": float(p.price_current), "profit": float(p.profit),
                   "leverage": 1.0} for p in rows])


@app.get("/v1/ticker")
@_auth
def ticker():
    mt5, _ = _mt5()
    symbol = request.args.get("symbol", "")
    if not symbol or not mt5.symbol_select(symbol, True):
        raise ValueError("unknown or unavailable MT5 symbol")
    tick = mt5.symbol_info_tick(symbol)
    if tick is None or not tick.bid or not tick.ask:
        raise RuntimeError("MT5 tick is unavailable")
    return _data({"bid": float(tick.bid), "ask": float(tick.ask),
                  "last": float(tick.last or 0), "volume": float(tick.volume or 0)})


@app.get("/v1/orderbook")
@_auth
def orderbook():
    mt5, _ = _mt5()
    symbol = request.args.get("symbol", "")
    if not symbol or not mt5.symbol_select(symbol, True):
        raise ValueError("unknown or unavailable MT5 symbol")
    tick = mt5.symbol_info_tick(symbol)
    if tick is None or tick.bid <= 0 or tick.ask <= 0:
        raise RuntimeError("MT5 bid/ask unavailable; this bridge exposes top-of-book only")
    # MT5 OTC/CFD instruments do not necessarily expose a centralized depth book.
    return _data({"bids": [[float(tick.bid), 0.0]], "asks": [[float(tick.ask), 0.0]], "depth_supported": False})


@app.get("/v1/candles")
@_auth
def candles():
    mt5, _ = _mt5()
    symbol = request.args.get("symbol", "")
    timeframe = request.args.get("timeframe", "15m").lower()
    limit = min(1000, max(1, int(request.args.get("limit", "100"))))
    timeframes = {"1m": mt5.TIMEFRAME_M1, "5m": mt5.TIMEFRAME_M5, "15m": mt5.TIMEFRAME_M15,
                  "30m": mt5.TIMEFRAME_M30, "1h": mt5.TIMEFRAME_H1, "4h": mt5.TIMEFRAME_H4,
                  "1d": mt5.TIMEFRAME_D1}
    if timeframe not in timeframes:
        raise ValueError("unsupported timeframe")
    if not symbol or not mt5.symbol_select(symbol, True):
        raise ValueError("unknown or unavailable MT5 symbol")
    rows = mt5.copy_rates_from_pos(symbol, timeframes[timeframe], 0, limit)
    if rows is None:
        raise RuntimeError(f"MT5 candle request failed: {mt5.last_error()}")
    return _data([{"timestamp": int(r["time"]), "open": float(r["open"]), "high": float(r["high"]),
                   "low": float(r["low"]), "close": float(r["close"]), "volume": float(r["tick_volume"])}
                  for r in rows])


@app.get("/v1/fees")
@_auth
def fees():
    # Broker commissions are account/instrument-specific and cannot be inferred reliably.
    # Require configured estimates rather than inventing them.
    maker = float(os.getenv("MT5_ESTIMATED_MAKER_FEE", "0"))
    taker = float(os.getenv("MT5_ESTIMATED_TAKER_FEE", "0"))
    return _data({"maker": maker, "taker": taker, "source": "operator_configured_estimate"})


@app.post("/v1/orders")
@_auth
def place_order():
    mt5, _ = _mt5()  # revalidate account mode immediately before every submission
    body = request.get_json(silent=True) or {}
    symbol = str(body.get("symbol", "")).strip()
    side = str(body.get("side", "")).upper()
    kind = str(body.get("type", "MARKET")).upper()
    client_order_id = str(body.get("client_order_id", "")).strip()
    volume = float(body.get("volume", 0))
    if not re.fullmatch(r"[A-Za-z0-9_.:-]{1,36}", client_order_id):
        raise ValueError("client_order_id must contain 1-36 alphanumeric or . _ : - characters")
    if side not in {"BUY", "SELL"} or volume <= 0:
        raise ValueError("side must be BUY/SELL and volume must be positive")
    if kind not in {"MARKET", "LIMIT", "STOP", "STOP_LIMIT"}:
        raise ValueError("unsupported order type")
    info = mt5.symbol_info(symbol)
    tick = mt5.symbol_info_tick(symbol)
    if info is None or tick is None or not info.visible:
        raise ValueError("symbol is not available in MT5 Market Watch")
    step = float(info.volume_step)
    if volume < float(info.volume_min) or volume > float(info.volume_max):
        raise ValueError("volume is outside broker limits")
    if step > 0 and abs(round(volume / step) * step - volume) > 1e-8:
        raise ValueError("volume does not match broker volume_step")

    is_buy = side == "BUY"
    if kind == "MARKET":
        request_type = mt5.ORDER_TYPE_BUY if is_buy else mt5.ORDER_TYPE_SELL
        price = float(tick.ask if is_buy else tick.bid)
        action = mt5.TRADE_ACTION_DEAL
    else:
        order_types = {
            ("LIMIT", True): mt5.ORDER_TYPE_BUY_LIMIT, ("LIMIT", False): mt5.ORDER_TYPE_SELL_LIMIT,
            ("STOP", True): mt5.ORDER_TYPE_BUY_STOP, ("STOP", False): mt5.ORDER_TYPE_SELL_STOP,
            ("STOP_LIMIT", True): mt5.ORDER_TYPE_BUY_STOP_LIMIT, ("STOP_LIMIT", False): mt5.ORDER_TYPE_SELL_STOP_LIMIT,
        }
        request_type = order_types[(kind, is_buy)]
        price = float(body.get("price") or 0)
        if price <= 0:
            raise ValueError("pending orders require a positive price")
        action = mt5.TRADE_ACTION_PENDING

    # Every new entry must be protected at submission time. Closing is a separate
    # by-ticket operation; this endpoint intentionally has no unprotected-entry mode.
    stop_loss = float(body.get("stop_loss") or 0)
    take_profit = float(body.get("take_profit") or 0)
    if stop_loss <= 0 or take_profit <= 0:
        raise ValueError("new MT5 entries require both stop_loss and take_profit")
    if is_buy and not stop_loss < price < take_profit:
        raise ValueError("BUY entry requires stop_loss < entry price < take_profit")
    if not is_buy and not take_profit < price < stop_loss:
        raise ValueError("SELL entry requires take_profit < entry price < stop_loss")

    digits = int(info.digits)
    price = round(price, digits)
    stop_loss = round(stop_loss, digits)
    take_profit = round(take_profit, digits)
    point = float(info.point or 0)
    min_stop_distance = float(getattr(info, "trade_stops_level", 0) or 0) * point
    if min_stop_distance > 0:
        if abs(price - stop_loss) < min_stop_distance or abs(take_profit - price) < min_stop_distance:
            raise ValueError("stop_loss/take_profit violate broker minimum stop distance")

    order = {
        "action": action, "symbol": symbol, "volume": volume, "type": request_type,
        "price": price, "sl": stop_loss, "tp": take_profit,
        "deviation": int(os.getenv("MT5_DEVIATION_POINTS", "20")),
        "magic": int(os.getenv("MT5_MAGIC_NUMBER", "260101")),
        "comment": f"STX:{client_order_id[:24]}",
        "type_time": mt5.ORDER_TIME_GTC, "type_filling": info.filling_mode,
    }
    stop_price = body.get("stop_price")
    if stop_price:
        order["stoplimit"] = round(float(stop_price), digits)

    request_hash = hashlib.sha256(
        json.dumps(body, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    ).hexdigest()
    store = _order_store()
    try:
        reservation = store.reserve(client_order_id, request_hash, body)
    except DuplicateOrderIdConflict as exc:
        return jsonify(ok=False, error=str(exc)), 409
    except UnresolvedBridgeOrder as exc:
        return jsonify(ok=False, error="ORDER_STATE_UNKNOWN", message=str(exc)), 409

    if reservation["kind"] == "duplicate":
        response_data = dict(reservation.get("response") or {})
        response_data["duplicate"] = True
        return _data(response_data)

    try:
        result = mt5.order_send(order)
    except Exception as exc:
        store.finish(client_order_id, "UNKNOWN", error=type(exc).__name__)
        return jsonify(
            ok=False, error="ORDER_STATE_UNKNOWN",
            message="MT5 submission raised an exception; reconcile terminal state before retrying.",
        ), 503
    if result is None:
        store.finish(client_order_id, "UNKNOWN", error="order_send returned None")
        return jsonify(
            ok=False, error="ORDER_STATE_UNKNOWN",
            message=f"MT5 submission result is ambiguous (last_error={mt5.last_error()}); reconcile before retrying.",
        ), 503

    code = int(result.retcode)
    done = code == int(mt5.TRADE_RETCODE_DONE)
    partial_code = int(getattr(mt5, "TRADE_RETCODE_DONE_PARTIAL", -1))
    placed = code == int(mt5.TRADE_RETCODE_PLACED)
    accepted = done or code == partial_code or placed
    status = "REJECTED" if not accepted else (
        "FILLED" if done else "PARTIALLY_FILLED" if code == partial_code else "PENDING"
    )
    response_data = {
        "order": int(getattr(result, "order", 0)), "deal": int(getattr(result, "deal", 0)),
        "status": status, "price": float(getattr(result, "price", 0)),
        "volume_filled": float(getattr(result, "volume", 0)), "commission": 0.0,
        "retcode": code, "comment": str(result.comment), "client_order_id": client_order_id,
    }
    ledger_status = {
        "PENDING": "ACCEPTED", "PARTIALLY_FILLED": "PARTIALLY_FILLED",
        "FILLED": "FILLED", "REJECTED": "REJECTED",
    }[status]
    store.finish(client_order_id, ledger_status, response=response_data)
    return _data(response_data)


@app.get("/v1/orders/by-client-id/<client_order_id>")
@_auth
def get_order_by_client_id(client_order_id: str):
    """Read the idempotency record; UNKNOWN records require explicit broker reconciliation."""
    record = _order_store().get(client_order_id)
    if record is None:
        return jsonify(ok=False, error="ORDER_NOT_FOUND"), 404
    return _data({
        "client_order_id": client_order_id,
        "status": record["status"],
        "response": record.get("response"),
        "created_at": record["created_at"],
        "updated_at": record["updated_at"],
        "error": record.get("error_text"),
        "manual_reconciliation_required": record["status"] == "UNKNOWN",
    })


@app.delete("/v1/orders/<order_id>")
@_auth
def cancel_order(order_id: str):
    mt5, _ = _mt5()
    ticket = int(order_id)
    result = mt5.order_send({"action": mt5.TRADE_ACTION_REMOVE, "order": ticket})
    if result is None:
        raise RuntimeError(f"MT5 cancel failed: {mt5.last_error()}")
    return _data({"cancelled": result.retcode == mt5.TRADE_RETCODE_DONE, "retcode": int(result.retcode)})


if __name__ == "__main__":
    # Keep loopback as the default; remote use must explicitly bind a private VPN IP.
    app.run(host=BIND_HOST, port=PORT, debug=False, threaded=True)


@app.post("/v1/positions/<position_ticket>/close")
@_auth
def close_position(position_ticket: str):
    """Close one hedge-mode position by its native ticket, never by symbol alone."""
    mt5, _ = _mt5()
    ticket = int(position_ticket)
    matches = mt5.positions_get(ticket=ticket)
    if matches is None or len(matches) != 1:
        raise ValueError("position ticket not found or ambiguous")
    position = matches[0]
    tick = mt5.symbol_info_tick(position.symbol)
    if tick is None:
        raise RuntimeError("cannot close position because current tick is unavailable")
    is_buy_position = int(position.type) == int(mt5.POSITION_TYPE_BUY)
    close_request = {
        "action": mt5.TRADE_ACTION_DEAL, "symbol": position.symbol,
        "position": ticket, "volume": float(position.volume),
        "type": mt5.ORDER_TYPE_SELL if is_buy_position else mt5.ORDER_TYPE_BUY,
        "price": float(tick.bid if is_buy_position else tick.ask),
        "deviation": int(os.getenv("MT5_DEVIATION_POINTS", "20")),
        "magic": int(os.getenv("MT5_MAGIC_NUMBER", "260101")),
        "comment": "Stratex demo close by ticket", "type_filling": mt5.symbol_info(position.symbol).filling_mode,
    }
    result = mt5.order_send(close_request)
    if result is None:
        raise RuntimeError(f"MT5 position close failed: {mt5.last_error()}")
    return _data({"ticket": ticket, "closed": result.retcode in {
        mt5.TRADE_RETCODE_DONE, getattr(mt5, "TRADE_RETCODE_DONE_PARTIAL", -1)},
        "retcode": int(result.retcode), "comment": str(result.comment)})
