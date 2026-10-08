"""api/nautilus_routes.py

REST API Blueprint exposing NautilusTrader Event-Driven Endpoints:
- GET /api/v1/nautilus/status
- POST /api/v1/nautilus/bars/aggregate
- POST /api/v1/nautilus/orders/bracket
- POST /api/v1/nautilus/risk/check
- POST /api/v1/nautilus/simulate

Every POST body is validated with :mod:`api.validation` before it reaches the
in-process engine. Previously ``float(payload.get("quantity"))`` and friends
turned any wrong-typed field into a 500, ``"NaN"`` prices passed straight into
the risk engine, a zero/negative bar step reached the aggregators, and ticks
for several symbols were silently merged into one symbol's bars.
"""

from __future__ import annotations

import time
from dataclasses import asdict
from datetime import datetime, timezone
from typing import Any

from flask import Blueprint, jsonify

from api.validation import (
    RequestValidationError,
    coerce_finite_float,
    get_float,
    get_list,
    get_str,
    get_symbol,
    json_body,
    object_item,
)
from stratex_nautilus_adapter import (
    BarType,
    OrderSide,
    OrderType,
    TradeTick,
    nt,
)

nautilus_bp = Blueprint("nautilus_bp", __name__, url_prefix="/api/v1/nautilus")

MAX_TICKS_PER_REQUEST = 50_000
_SIDES = tuple(s.value for s in OrderSide)
_ORDER_TYPES = tuple(t.value for t in OrderType)
_BAR_TYPES = tuple(b.value for b in BarType)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _rejected(message: str, status: int = 400):
    return jsonify({"status": "ERROR", "error": "REJECTED", "message": message}), status


def _parse_tick(field: str, raw: Any, default_symbol: str) -> TradeTick:
    tick = object_item(field, raw)
    symbol = get_symbol(tick, "symbol", default_symbol)
    price = coerce_finite_float(f"{field}.price", tick.get("price"))
    if price <= 0:
        raise RequestValidationError(f"{field}.price", "must be > 0")
    size = coerce_finite_float(f"{field}.size", tick.get("size", 1.0))
    if size <= 0:
        raise RequestValidationError(f"{field}.size", "must be > 0")
    ts_raw = tick.get("ts_event", time.time_ns())
    if isinstance(ts_raw, bool) or not isinstance(ts_raw, int) or ts_raw < 0:
        raise RequestValidationError(f"{field}.ts_event", "must be a non-negative integer (nanoseconds)")
    side = get_str(tick, "side", "BUY", upper=True, choices=_SIDES)
    return TradeTick(symbol=symbol, price=price, size=size, ts_event=ts_raw, side=OrderSide(side))


def _parse_ticks(payload: dict[str, Any], *, require_single_symbol: bool) -> list[TradeTick]:
    raw_ticks = get_list(payload, "ticks", [], max_len=MAX_TICKS_PER_REQUEST)
    default_symbol = get_symbol(payload, "symbol", "BTCUSDT")
    ticks = [_parse_tick(f"ticks[{i}]", raw, default_symbol) for i, raw in enumerate(raw_ticks)]
    if require_single_symbol and len({t.symbol for t in ticks}) > 1:
        raise RequestValidationError("ticks", "must all belong to one symbol (bars are per-symbol)")
    for index in range(1, len(ticks)):
        if ticks[index].ts_event < ticks[index - 1].ts_event:
            raise RequestValidationError(
                f"ticks[{index}].ts_event", "ticks must be ordered by non-decreasing ts_event",
            )
    return ticks


@nautilus_bp.route("/status", methods=["GET"])
def get_status():
    """Returns overall Nautilus engine status and telemetry."""
    return jsonify({
        "status": "OK",
        "timestamp": _now(),
        "data": nt.get_status(),
    }), 200


@nautilus_bp.route("/bars/aggregate", methods=["POST"])
def aggregate_bars():
    """Aggregates a stream of raw trade ticks into completed bars."""
    payload = json_body()
    bar_type = get_str(payload, "bar_type", "TICK", upper=True, choices=_BAR_TYPES)
    step = get_float(payload, "step", 10.0, gt=0, max=1e12)
    if bar_type == BarType.TICK.value and (step < 1 or not float(step).is_integer()):
        raise RequestValidationError("step", "must be a whole number >= 1 for TICK bars")
    ticks = _parse_ticks(payload, require_single_symbol=True)

    try:
        bars = nt.aggregate(ticks, bar_type=BarType(bar_type), step=step)
    except (ValueError, ZeroDivisionError) as e:
        return _rejected(str(e))
    return jsonify({
        "status": "OK",
        "timestamp": _now(),
        "data": [asdict(b) for b in bars],
    }), 200


@nautilus_bp.route("/orders/bracket", methods=["POST"])
def create_bracket():
    """Creates an institutional bracket order (Entry + Take Profit + Stop Loss)."""
    payload = json_body()
    symbol = get_symbol(payload, "symbol", "BTCUSDT")
    side = get_str(payload, "side", "BUY", upper=True, choices=_SIDES)
    quantity = get_float(payload, "quantity", 0.01, gt=0)
    entry_price = get_float(payload, "entry_price", 50000.0, gt=0)
    take_profit_price = get_float(payload, "take_profit_price", 52000.0, gt=0)
    stop_loss_price = get_float(payload, "stop_loss_price", 49000.0, gt=0)
    entry_type = get_str(payload, "entry_type", "LIMIT", upper=True, choices=_ORDER_TYPES)

    # A bracket whose protective legs sit on the wrong side of the entry is not
    # a bracket: the "stop" would fill immediately or the "target" never could.
    if side == "BUY" and not stop_loss_price < entry_price < take_profit_price:
        return _rejected("BUY bracket requires stop_loss_price < entry_price < take_profit_price")
    if side == "SELL" and not take_profit_price < entry_price < stop_loss_price:
        return _rejected("SELL bracket requires take_profit_price < entry_price < stop_loss_price")

    try:
        bracket = nt.create_bracket(
            symbol=symbol,
            side=side,
            quantity=quantity,
            entry_price=entry_price,
            take_profit_price=take_profit_price,
            stop_loss_price=stop_loss_price,
            entry_type=entry_type,
        )
    except PermissionError as e:
        return _rejected(str(e), 403)
    except ValueError as e:
        return _rejected(str(e))
    return jsonify({
        "status": "OK",
        "timestamp": _now(),
        "data": asdict(bracket),
    }), 201


@nautilus_bp.route("/risk/check", methods=["POST"])
def check_risk():
    """Evaluates an order against pre-trade institutional risk rules."""
    payload = json_body()
    symbol = get_symbol(payload, "symbol", "BTCUSDT")
    side = get_str(payload, "side", "BUY", upper=True, choices=_SIDES)
    quantity = get_float(payload, "quantity", 0.01, gt=0)
    price = get_float(payload, "price", 50000.0, gt=0)

    try:
        result = nt.check_risk(symbol=symbol, side=side, quantity=quantity, price=price)
    except ValueError as e:
        return _rejected(str(e))
    return jsonify({
        "status": "OK",
        "timestamp": _now(),
        "data": asdict(result),
    }), 200


@nautilus_bp.route("/simulate", methods=["POST"])
def simulate_ticks():
    """Simulates deterministic order matching against incoming ticks."""
    payload = json_body()
    ticks = _parse_ticks(payload, require_single_symbol=False)

    try:
        fills = nt.simulate(ticks)
    except ValueError as e:
        return _rejected(str(e))
    return jsonify({
        "status": "OK",
        "timestamp": _now(),
        "data": {
            "fills": [asdict(f) for f in fills],
            "fills_count": len(fills),
        },
    }), 200
