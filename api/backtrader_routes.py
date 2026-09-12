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
from dataclasses import asdict
from flask import Blueprint, jsonify, request
import pandas as pd

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


@backtrader_bp.route("/run", methods=["POST"])
def run_backtest():
    """Executes a backtest using Cerebro over provided OHLCV candles."""
    payload = request.get_json(force=True, silent=True) or {}
    candles = payload.get("candles", [])
    if not candles:
        return jsonify({"status": "ERROR", "message": "No candle data provided."}), 400

    df = pd.DataFrame(candles)
    strategy_name = payload.get("strategy", "SMACross")
    sizer_name = payload.get("sizer", "PercentSizer")
    initial_cash = float(payload.get("initial_cash", 10000.0))
    commission_pct = float(payload.get("commission_pct", 0.0005))
    params = payload.get("params", {})

    strategy_cls = bt.strategies.get(strategy_name, SMACrossStrategy)
    sizer_cls = getattr(bt.sizers, sizer_name, bt.sizers.PercentSizer)

    try:
        cerebro = Cerebro()
        cerebro.set_cash(initial_cash)
        cerebro.set_commission(commission_pct=commission_pct)
        cerebro.add_data(df, name="MAIN")
        cerebro.add_strategy(strategy_cls, **params)
        cerebro.set_sizer(sizer_cls)

        res = cerebro.run()
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
            },
        }), 200
    except Exception as e:
        return jsonify({"status": "ERROR", "message": str(e)}), 400


@backtrader_bp.route("/analyze", methods=["POST"])
def analyze_trades():
    """Computes quantitative metrics directly from a list of completed trades."""
    payload = request.get_json(force=True, silent=True) or {}
    trades_data = payload.get("trades", [])

    trade_analyzer = TradeAnalyzer()
    sqn_analyzer = SQN()

    for idx, t in enumerate(trades_data):
        pnl = float(t.get("pnl", 0.0))
        trade = TradeRecord(
            trade_id=f"t_{idx}",
            symbol=str(t.get("symbol", "BTCUSDT")),
            side=OrderSide.BUY,
            size=float(t.get("size", 1.0)),
            entry_price=float(t.get("entry_price", 100.0)),
            exit_price=float(t.get("exit_price", 100.0 + pnl)),
            entry_idx=idx,
            exit_idx=idx + 1,
            pnl=pnl,
            pnl_pct=float(t.get("pnl_pct", 0.0)),
            commission=float(t.get("commission", 0.0)),
            duration_bars=1,
        )
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
