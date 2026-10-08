"""api/backtrader_routes.py

REST API Blueprint exposing Backtrader Endpoints:
- GET /api/v1/backtrader/status
- GET /api/v1/backtrader/sizers
- GET /api/v1/backtrader/analyzers
- POST /api/v1/backtrader/run
- POST /api/v1/backtrader/analyze
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import pandas as pd
from flask import Blueprint, jsonify

from api.validation import (
    RequestValidationError,
    coerce_finite_float,
    get_dict,
    get_float,
    get_int,
    get_list,
    get_str,
    json_body,
    object_item,
)

from stratex_backtrader_adapter import (
    bt,
    Cerebro,
    SMACrossStrategy,
    RSIStrategy,
    TradeRecord,
    TradeAnalyzer,
    SQN,
    OrderSide,
)

backtrader_bp = Blueprint("backtrader_bp", __name__, url_prefix="/api/v1/backtrader")


@backtrader_bp.route("/status", methods=["GET"])
def get_status():
    """Returns overall Backtrader engine status and catalog."""
    return jsonify({
        "status": "OK",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "data": bt.get_status(),
    }), 200


@backtrader_bp.route("/sizers", methods=["GET"])
def list_sizers():
    """Returns available position sizing engines."""
    return jsonify({
        "status": "OK",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "data": [
            {"name": "FixedSize", "description": "Constant lot or contract sizing per trade"},
            {"name": "PercentSizer", "description": "Allocates fixed percentage of current portfolio equity"},
            {"name": "VolatilitySizer", "description": "ATR volatility-adjusted risk budgeting"},
            {"name": "KellySizer", "description": "Mathematical Kelly criterion sizing based on win rate & payoff"},
        ],
    }), 200


@backtrader_bp.route("/analyzers", methods=["GET"])
def list_analyzers():
    """Returns available institutional performance analyzers."""
    return jsonify({
        "status": "OK",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "data": [
            {"name": "SharpeRatio", "description": "Annualized risk-adjusted return ratio"},
            {"name": "SortinoRatio", "description": "Downside deviation risk-adjusted return ratio"},
            {"name": "DrawDown", "description": "Peak equity, maximum drawdown %, and duration in bars"},
            {"name": "TradeAnalyzer", "description": "Win rate, profit factor, payoff ratio, streaks, and avg PnL"},
            {"name": "SQN", "description": "Van Tharp System Quality Number"},
            {"name": "CalmarRatio", "description": "Annualized return over maximum drawdown"},
        ],
    }), 200


# Per-strategy parameter allow-list: name -> (min, max, integer?)
_STRATEGY_PARAMS: dict[str, dict[str, tuple[float, float, bool]]] = {
    "SMACross": {"fast_period": (1, 1000, True), "slow_period": (2, 5000, True)},
    "RSI": {"period": (2, 1000, True), "lower": (0, 100, False), "upper": (0, 100, False)},
}
_SIZERS = ("FixedSize", "PercentSizer", "VolatilitySizer", "KellySizer")
MAX_CANDLES = 100_000
_OHLCV = ("open", "high", "low", "close", "volume")


def _rejected(message: str, status: int = 400):
    return jsonify({"status": "ERROR", "error": "REJECTED", "message": message}), status


def _parse_candles(payload: dict[str, Any]) -> pd.DataFrame:
    candles = get_list(payload, "candles", [], max_len=MAX_CANDLES, item=object_item)
    if not candles:
        raise RequestValidationError("candles", "at least one OHLCV candle is required")
    rows = []
    for index, candle in enumerate(candles):
        lowered = {str(k).lower(): v for k, v in candle.items()}
        if "close" not in lowered:
            raise RequestValidationError(f"candles[{index}]", "must contain a 'close' value")
        row = {}
        for column in _OHLCV:
            if column in lowered and lowered[column] is not None:
                row[column] = coerce_finite_float(f"candles[{index}].{column}", lowered[column])
        if row["close"] <= 0 or any(row.get(c, 1.0) <= 0 for c in ("open", "high", "low")):
            raise RequestValidationError(f"candles[{index}]", "prices must be > 0")
        if row.get("volume", 0.0) < 0:
            raise RequestValidationError(f"candles[{index}].volume", "must be >= 0")
        if "high" in row and "low" in row and row["high"] < row["low"]:
            raise RequestValidationError(f"candles[{index}]", "high must be >= low")
        rows.append(row)
    return pd.DataFrame(rows)


def _parse_params(payload: dict[str, Any], strategy_name: str) -> dict[str, Any]:
    raw = get_dict(payload, "params", {})
    allowed = _STRATEGY_PARAMS[strategy_name]
    unknown = sorted(set(raw) - set(allowed))
    if unknown:
        raise RequestValidationError("params", f"unknown parameter(s) {unknown} for {strategy_name}; allowed: {sorted(allowed)}")
    parsed: dict[str, Any] = {}
    for name, (low, high, integer) in allowed.items():
        if name not in raw:
            continue
        if integer:
            parsed[name] = get_int(raw, name, min=int(low), max=int(high))
        else:
            parsed[name] = get_float(raw, name, min=low, max=high)
    if strategy_name == "SMACross" and parsed.get("fast_period", 10) >= parsed.get("slow_period", 30):
        raise RequestValidationError("params", "fast_period must be < slow_period")
    if strategy_name == "RSI" and parsed.get("lower", 30) >= parsed.get("upper", 70):
        raise RequestValidationError("params", "lower must be < upper")
    return parsed


@backtrader_bp.route("/run", methods=["POST"])
def run_backtest():
    """Executes a backtest using Cerebro over provided OHLCV candles."""
    payload = json_body()
    df = _parse_candles(payload)
    strategy_name = get_str(payload, "strategy", "SMACross", choices=tuple(_STRATEGY_PARAMS))
    sizer_name = get_str(payload, "sizer", "PercentSizer", choices=_SIZERS)
    initial_cash = get_float(payload, "initial_cash", 10000.0, gt=0, max=1e12)
    commission_pct = get_float(payload, "commission_pct", 0.0005, min=0, lt=0.1)
    params = _parse_params(payload, strategy_name)

    strategy_cls = bt.strategies[strategy_name]
    sizer_cls = getattr(bt.sizers, sizer_name)

    try:
        cerebro = Cerebro()
        cerebro.set_cash(initial_cash)
        cerebro.set_commission(commission_pct=commission_pct)
        cerebro.add_data(df, name="MAIN")
        cerebro.add_strategy(strategy_cls, **params)
        cerebro.set_sizer(sizer_cls)
        res = cerebro.run()
    except (ValueError, ZeroDivisionError, KeyError) as e:
        return _rejected(str(e))
    return jsonify({
        "status": "OK",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "data": {
            "strategy": res.strategy_name,
            "starting_cash": res.starting_cash,
            "ending_cash": res.ending_cash,
            "total_pnl": res.total_pnl,
            "total_return_pct": res.total_return_pct,
            "total_trades": len(res.trades),
            "analyzers": res.analyzers,
            "evidence_class": "caller_supplied_data_backtest",
        },
    }), 200


@backtrader_bp.route("/analyze", methods=["POST"])
def analyze_trades():
    """Computes quantitative metrics directly from a list of completed trades."""
    payload = json_body()
    trades_data = get_list(payload, "trades", [], max_len=MAX_CANDLES, item=object_item)

    trade_analyzer = TradeAnalyzer()
    sqn_analyzer = SQN()

    for idx, t in enumerate(trades_data):
        field = f"trades[{idx}]"
        pnl = get_float(t, "pnl", 0.0)
        entry_price = get_float(t, "entry_price", 100.0, gt=0)
        trade = TradeRecord(
            trade_id=f"t_{idx}",
            symbol=get_str(t, "symbol", "BTCUSDT", max_len=32),
            side=OrderSide.BUY,
            size=get_float(t, "size", 1.0, gt=0),
            entry_price=entry_price,
            exit_price=get_float(t, "exit_price", entry_price + pnl),
            entry_idx=idx,
            exit_idx=idx + 1,
            pnl=pnl,
            pnl_pct=get_float(t, "pnl_pct", 0.0),
            commission=get_float(t, "commission", 0.0, min=0),
            duration_bars=1,
        )
        if trade.exit_price <= 0:
            raise RequestValidationError(f"{field}.exit_price", "must be > 0")
        trade_analyzer.notify_trade(trade)
        sqn_analyzer.notify_trade(trade)

    return jsonify({
        "status": "OK",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "data": {
            "trade_analysis": trade_analyzer.get_analysis(),
            "sqn": sqn_analyzer.get_analysis(),
        },
    }), 200
