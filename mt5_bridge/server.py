"""Demo-only Flask bridge running beside a local MetaTrader 5 terminal.

This module is intended for a Windows VPS. It intentionally refuses live accounts.
Expose only over a private VPN/firewall and use a high-entropy bearer token.
"""
from __future__ import annotations

import os
from functools import wraps
from typing import Any

from flask import Flask, jsonify, request

app = Flask(__name__)
TOKEN = os.getenv("MT5_BRIDGE_TOKEN", "")
EXPECTED_SERVER = os.getenv("MT5_EXPECTED_SERVER", "ICMarketsSC-Demo")
BIND_HOST = os.getenv("MT5_BRIDGE_BIND", "127.0.0.1")
PORT = int(os.getenv("MT5_BRIDGE_PORT", "8765"))


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
        except (RuntimeError, ValueError, KeyError) as exc:
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
    mt5, _ = _mt5()  # account-mode validation occurs immediately before every order
    body = request.get_json(silent=True) or {}
    symbol = str(body.get("symbol", ""))
    side = str(body.get("side", "")).upper()
    kind = str(body.get("type", "MARKET")).upper()
    volume = float(body.get("volume", 0))
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
    order = {"action": action, "symbol": symbol, "volume": volume, "type": request_type,
             "price": price, "deviation": int(os.getenv("MT5_DEVIATION_POINTS", "20")),
             "magic": int(os.getenv("MT5_MAGIC_NUMBER", "260101")), "comment": "Stratex demo",
             "type_time": mt5.ORDER_TIME_GTC, "type_filling": info.filling_mode}
    stop_price = body.get("stop_price")
    if stop_price:
        order["stoplimit"] = float(stop_price)
    result = mt5.order_send(order)
    if result is None:
        raise RuntimeError(f"MT5 order_send failed: {mt5.last_error()}")
    status = "REJECTED" if result.retcode not in {mt5.TRADE_RETCODE_DONE, mt5.TRADE_RETCODE_PLACED,
                                                  getattr(mt5, "TRADE_RETCODE_DONE_PARTIAL", -1)} else (
        "FILLED" if result.retcode == mt5.TRADE_RETCODE_DONE else "PENDING")
    return _data({"order": int(getattr(result, "order", 0)), "deal": int(getattr(result, "deal", 0)),
                  "status": status, "price": float(getattr(result, "price", 0)),
                  "volume_filled": float(getattr(result, "volume", 0)),
                  "commission": 0.0, "retcode": int(result.retcode), "comment": str(result.comment)})


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
