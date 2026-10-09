"""
api/public_status.py — Comprehensive Public Read-Only Ecosystem Status API Blueprint.

Endpoints:
- GET /api/v1/status : Complete bot status rollup.
- GET /api/v1/positions : All currently open positions with unrealized PnL.
- GET /api/v1/trades : Recent closed trades with pagination support.
- GET /api/v1/strategies : Per-strategy performance metrics.
- GET /api/v1/advisory : AI advisory subsystem status & recent decisions.
- GET /api/v1/risk : Real-time risk metrics & drawdown limit proximity.
- GET /api/v1/history/equity : Historical equity curve data points.
"""

import json
import os
from datetime import datetime, timezone
from pathlib import Path

from flask import Blueprint, jsonify, request

from advisory_ledger import read_recent_advisory_entries
from advisory_params import get_advisory_overlay
from api.auth import require_permission
from api.data_shapes import format_api_response
from api.validation import query_int

public_status_bp = Blueprint("public_status", __name__, url_prefix="/api/v1")
PROJECT_ROOT = Path(__file__).resolve().parents[1]
HEARTBEAT_MAX_AGE_SECONDS = 180


def _read_json(name: str) -> dict:
    try:
        value = json.loads((PROJECT_ROOT / name).read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError, TypeError):
        return {}


def _read_jsonl(name: str) -> list[dict]:
    rows = []
    try:
        with (PROJECT_ROOT / name).open(encoding="utf-8") as stream:
            for line in stream:
                if not line.strip():
                    continue
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                if isinstance(row, dict):
                    rows.append(row)
    except OSError:
        pass
    return rows


def _timestamp_seconds(value) -> float | None:
    try:
        if isinstance(value, (int, float)):
            return float(value)
        if isinstance(value, str):
            return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except (TypeError, ValueError, OverflowError):
        return None
    return None


def _closed_trade_metrics() -> dict:
    trades = [
        row for row in _read_jsonl("paper_trade_ledger.jsonl")
        if row.get("status") == "CLOSED" or row.get("closed_at") or row.get("exit_timestamp")
    ]
    pnl_values = []
    for trade in trades:
        pnl = trade.get("net_pnl", trade.get("pnl"))
        try:
            pnl = float(pnl)
        except (TypeError, ValueError):
            continue
        if pnl == pnl and abs(pnl) != float("inf"):
            pnl_values.append((trade, pnl))
    wins = [pnl for _, pnl in pnl_values if pnl > 0]
    losses = [pnl for _, pnl in pnl_values if pnl < 0]
    gross_loss = abs(sum(losses))
    return {
        "trades": len(pnl_values),
        "win_rate": (len(wins) / len(pnl_values) * 100) if pnl_values else None,
        "profit_factor": (sum(wins) / gross_loss) if gross_loss else None,
        "net_pnl": sum(pnl for _, pnl in pnl_values) if pnl_values else None,
        "last_trade_timestamp": next((
            trade.get("closed_at") or trade.get("exit_timestamp") or trade.get("timestamp")
            for trade, _ in reversed(pnl_values)
            if trade.get("closed_at") or trade.get("exit_timestamp") or trade.get("timestamp")
        ), None),
    }


def _fresh_paper_runner() -> tuple[dict, bool, float | None]:
    heartbeat = _read_json("paper_runner_heartbeat.json")
    stamp = _timestamp_seconds(heartbeat.get("timestamp"))
    age = max(0.0, datetime.now(timezone.utc).timestamp() - stamp) if stamp is not None else None
    fresh = bool(
        heartbeat.get("alive") is True
        and heartbeat.get("status") == "RUNNING"
        and age is not None
        and age <= HEARTBEAT_MAX_AGE_SECONDS
    )
    return heartbeat, fresh, age


def _fresh_engine_snapshot() -> list[str]:
    snapshot = _read_json("engine-health.json")
    stamp = _timestamp_seconds(snapshot.get("timestamp"))
    age = max(0.0, datetime.now(timezone.utc).timestamp() - stamp) if stamp is not None else None
    strategies = snapshot.get("strategies", [])
    return strategies if age is not None and age <= HEARTBEAT_MAX_AGE_SECONDS and isinstance(strategies, list) else []


def _add_cors_headers(response):
    response.headers["Access-Control-Allow-Origin"] = "*"
    response.headers["Access-Control-Allow-Headers"] = "Content-Type, X-API-Key, Authorization"
    response.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
    return response


@public_status_bp.after_request
def apply_cors_and_caching(response):
    return _add_cors_headers(response)


@public_status_bp.route("/status", methods=["GET"])
@require_permission("read")
def get_bot_status():
    """Return persisted paper telemetry with freshness and evidence stated."""
    overlay = get_advisory_overlay()
    recent_adv = read_recent_advisory_entries(limit=1)
    last_adv = recent_adv[0] if recent_adv else None

    heartbeat, runner_fresh, heartbeat_age = _fresh_paper_runner()
    curve = _read_jsonl("paper_equity_curve.jsonl")
    latest_curve = curve[-1] if curve else {}
    curve_stamp = _timestamp_seconds(latest_curve.get("timestamp"))
    curve_age = max(0.0, datetime.now(timezone.utc).timestamp() - curve_stamp) if curve_stamp is not None else None
    metrics = _closed_trade_metrics()
    portfolio = _read_json("paper_portfolio.json")
    equity = latest_curve.get("equity")
    realized_pnl = latest_curve.get("realized_pnl")
    unrealized_pnl = latest_curve.get("unrealized_pnl")
    positions = portfolio.get("positions", {})
    open_positions = [
        position for position in positions.values()
        if isinstance(position, dict) and position.get("status") == "OPEN"
    ] if isinstance(positions, dict) else []

    overlay_state = overlay.get_state()
    active_overrides = overlay_state.get("active_overrides", {})

    status_data = {
        "mode": os.getenv("TRADING_MODE", "PAPER").upper(),
        "trading_active": runner_fresh,
        "runner_status": "RUNNING" if runner_fresh else "STALE_OR_UNAVAILABLE",
        "runner_heartbeat_age_seconds": heartbeat_age,
        "last_persisted_sample_age_seconds": curve_age,
        "persisted_sample_stale": curve_age is None or curve_age > HEARTBEAT_MAX_AGE_SECONDS,
        "evidence_status": "PAPER_LEDGER" if metrics["trades"] else "NO_CLOSED_TRADE_LEDGER",
        "equity": equity,
        "unrealized_pnl": unrealized_pnl,
        "realized_pnl": realized_pnl,
        "daily_pnl": None,
        "daily_pnl_display": None,
        "win_rate": metrics["win_rate"],
        "profit_factor": metrics["profit_factor"],
        "max_drawdown_pct": None,
        "open_positions_count": len(open_positions) if runner_fresh else None,
        "strategies_active": _fresh_engine_snapshot() if runner_fresh else [],
        "advisory_status": {
            "shadow_mode": os.getenv("TESTNET_ADVISORY_SHADOW_MODE", "True").lower() == "true",
            "active_overrides_count": len(active_overrides),
            "last_decision": last_adv.get("decision_id") if last_adv else "NONE",
            "last_verdict": last_adv.get("verdict") if last_adv else "NO_DATA"
        },
        "risk_status": {
            "daily_loss_pct": None,
            "drawdown_pct": None,
            "max_drawdown_limit_pct": None,
            "drawdown_headroom_pct": None,
            "risk_state": "UNKNOWN"
        },
        "uptime_seconds": None,
        "last_trade_timestamp": metrics["last_trade_timestamp"],
        "heartbeat_error": heartbeat.get("last_error"),
    }
    return jsonify(format_api_response(status_data))


@public_status_bp.route("/positions", methods=["GET"])
@require_permission("read")
def get_positions():
    """Return open paper positions from the current portfolio snapshot only."""
    _, runner_fresh, _ = _fresh_paper_runner()
    portfolio = _read_json("paper_portfolio.json")
    stored = portfolio.get("positions", {})
    positions = [
        {"position_id": key, **value}
        for key, value in stored.items()
        if runner_fresh and isinstance(value, dict) and value.get("status") == "OPEN"
    ] if isinstance(stored, dict) else []
    return jsonify(format_api_response(positions, error=None if runner_fresh else "PAPER_POSITION_SNAPSHOT_STALE_OR_UNAVAILABLE"))


@public_status_bp.route("/trades", methods=["GET"])
@require_permission("read")
def get_recent_trades():
    """Returns paginated closed trade history."""
    page = query_int("page", 1, min=1, max=1_000_000)
    limit = query_int("limit", 20, min=1, max=100)

    trades = list(reversed([
        row for row in _read_jsonl("paper_trade_ledger.jsonl")
        if row.get("status") == "CLOSED" or row.get("closed_at") or row.get("exit_timestamp")
    ]))
    total_count = len(trades)
    start_idx = (page - 1) * limit
    page_trades = trades[start_idx : start_idx + limit]

    pagination_info = {
        "page": page,
        "limit": limit,
        "total_items": total_count,
        "total_pages": max(1, (total_count + limit - 1) // limit)
    }
    return jsonify(format_api_response(page_trades, pagination=pagination_info))


@public_status_bp.route("/strategies", methods=["GET"])
@require_permission("read")
def get_strategies_breakdown():
    """Return governance status; metrics stay unknown without a real closed ledger."""
    try:
        from config_strategy import PRODUCTION_STRATEGY_REGISTRY
    except Exception:
        PRODUCTION_STRATEGY_REGISTRY = {}
    metrics = _closed_trade_metrics()
    strat_data = {
        name: {
            "status": spec.get("status", "UNKNOWN"),
            "trades": None,
            "win_rate": None,
            "profit_factor": None,
            "net_pnl": None,
            "evidence_status": "NO_CLOSED_TRADE_LEDGER",
        }
        for name, spec in PRODUCTION_STRATEGY_REGISTRY.items()
    }
    if metrics["trades"]:
        strat_data["ledger_summary"] = {
            "status": "PAPER_LEDGER_AGGREGATE",
            **metrics,
            "evidence_status": "PAPER_LEDGER_AGGREGATE_NOT_PER_STRATEGY",
        }
    return jsonify(format_api_response(strat_data))


@public_status_bp.route("/advisory", methods=["GET"])
@require_permission("read")
def get_advisory_status():
    """Returns AI advisory status and recent consultation verdicts."""
    overlay = get_advisory_overlay()
    overlay_state = overlay.get_state()
    recent = read_recent_advisory_entries(limit=10)
    data = {
        "shadow_mode": os.getenv("TESTNET_ADVISORY_SHADOW_MODE", "True").lower() == "true",
        "active_overrides": overlay_state.get("active_overrides", {}),
        "recent_decisions": recent
    }
    return jsonify(format_api_response(data))


@public_status_bp.route("/risk", methods=["GET"])
@require_permission("read")
def get_risk_metrics():
    """Return configured limits; do not invent live risk measurements."""
    data = {
        "current_drawdown_pct": None,
        "max_drawdown_limit_pct": None,
        "drawdown_headroom_pct": None,
        "daily_loss_pct": None,
        "max_daily_loss_limit_pct": None,
        "daily_loss_headroom_pct": None,
        "circuit_breaker_status": "UNKNOWN",
        "var_95_pct": None,
        "cvar_95_pct": None,
        "evidence_status": "LIVE_RISK_SNAPSHOT_UNAVAILABLE",
    }
    return jsonify(format_api_response(data))


@public_status_bp.route("/history/equity", methods=["GET"])
@require_permission("read")
def get_equity_history():
    """Return persisted equity samples, never seeded display values."""
    max_points = query_int("max_points", 2000, min=2, max=10_000)
    points = [
        {"timestamp": row.get("timestamp"), "equity": row.get("equity"), "realized_pnl": row.get("realized_pnl"), "unrealized_pnl": row.get("unrealized_pnl")}
        for row in _read_jsonl("paper_equity_curve.jsonl")
        if row.get("timestamp") is not None and row.get("equity") is not None
    ]
    total = len(points)
    if total > max_points:
        # Uniform stride over the persisted samples (first and newest kept);
        # thinning is disclosed, nothing is interpolated.
        step = (total - 1) / (max_payload := max_points - 1)
        points = [points[round(i * step)] for i in range(max_payload + 1)]
    payload = format_api_response(points)
    payload["series"] = {"source_count": total, "returned": len(points), "downsampled": total > len(points)}
    return jsonify(payload)
