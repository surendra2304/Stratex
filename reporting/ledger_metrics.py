"""Measured, period-bounded trading metrics for truthful reports.

Metrics in this module are derived only from complete JSONL evidence. Missing or
corrupt inputs remain unavailable; they are never replaced with demo-looking
numbers. The helper intentionally excludes route-census synthetic records.
"""
from __future__ import annotations

import math
import os
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any

from atomic_io import read_jsonl

_EQUITY_BOUNDARY_TOLERANCE = timedelta(hours=1)
_CLOSED_STATUSES = frozenset({"CLOSED", "EXIT_FILLED", "CLOSE_FILLED", "CLOSED_FILLED"})
_TIME_FIELDS = ("closed_at", "exit_timestamp", "exit_time", "close_timestamp", "timestamp")
_PNL_FIELDS = ("net_pnl", "pnl", "realized_pnl")


def utc_iso(value: datetime) -> str:
    """Format an aware datetime as a stable ISO-8601 UTC timestamp."""
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def parse_timestamp(value: Any) -> datetime | None:
    """Parse a persisted timestamp without treating invalid values as current time."""
    try:
        if isinstance(value, bool) or value is None:
            return None
        if isinstance(value, (int, float)):
            stamp = float(value)
            if not math.isfinite(stamp):
                return None
            # Accommodate common millisecond epoch fields without guessing for
            # ordinary second-based values.
            if abs(stamp) >= 100_000_000_000:
                stamp /= 1000.0
            return datetime.fromtimestamp(stamp, tz=timezone.utc)
        if not isinstance(value, str) or not value.strip():
            return None
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            # Legacy Stratex writers used utcnow().isoformat() + "Z"; a naive
            # persisted ISO timestamp is therefore treated as UTC, documented.
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except (TypeError, ValueError, OverflowError, OSError):
        return None


def finite_number(value: Any) -> float | None:
    """Accept real finite numeric evidence, rejecting booleans and numeric text."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def default_evidence_paths(mode: str | None = None) -> dict[str, str | None]:
    """Resolve a mode-specific ledger pair without silently crossing environments."""
    normalized = (mode or os.getenv("TRADING_MODE", "TESTNET")).strip().upper()
    if normalized in {"TESTNET", "FUTURES"}:
        return {
            "trading_mode": normalized,
            "ledger_path": os.getenv("TESTNET_LEDGER_FILE", "testnet_trade_ledger.jsonl"),
            "equity_path": os.getenv("TESTNET_EQUITY_HISTORY_FILE", "testnet_equity_history.jsonl"),
        }
    if normalized in {"PAPER", "PAPER_TRADING", "DRY_RUN", "SIMULATION"}:
        return {
            "trading_mode": "PAPER",
            "ledger_path": os.getenv("PAPER_TRADE_LEDGER_FILE", "paper_trade_ledger.jsonl"),
            "equity_path": os.getenv("PAPER_EQUITY_CURVE_FILE", "paper_equity_curve.jsonl"),
        }
    if normalized == "LIVE":
        return {
            "trading_mode": normalized,
            "ledger_path": os.getenv("LIVE_TRADE_LEDGER_FILE", "live_trade_ledger.jsonl"),
            "equity_path": os.getenv("LIVE_EQUITY_CURVE_FILE", "live_equity_curve.jsonl"),
        }
    return {"trading_mode": normalized or "UNKNOWN", "ledger_path": None, "equity_path": None}


def _load_jsonl(path_value: str | os.PathLike[str] | None) -> tuple[list[dict[str, Any]], str, int]:
    """Read a complete JSONL source or report why it cannot support metrics."""
    if not path_value:
        return [], "UNSUPPORTED_MODE", 0
    path = Path(path_value)
    try:
        if not path.is_file():
            return [], "MISSING", 0
        result = read_jsonl(path, expected_type=dict)
    except OSError:
        return [], "UNREADABLE", 0
    if not result.clean:
        return [], "INCOMPLETE", result.skipped
    if not result.records:
        return [], "EMPTY", 0
    return result.records, "COMPLETE", 0


def _is_closed_trade(row: dict[str, Any]) -> bool:
    """Identify terminal trade records without counting synthetic census events."""
    if row.get("census_synthetic") is True or row.get("synthetic") is True:
        return False
    status = row.get("status")
    status_text = status.strip().upper() if isinstance(status, str) else ""
    has_close_time = any(row.get(key) is not None for key in _TIME_FIELDS[:-1])
    return status_text in _CLOSED_STATUSES or has_close_time


def _trade_timestamp(row: dict[str, Any]) -> datetime | None:
    for key in _TIME_FIELDS:
        parsed = parse_timestamp(row.get(key))
        if parsed is not None:
            return parsed
    return None


def _trade_pnl(row: dict[str, Any]) -> float | None:
    for key in _PNL_FIELDS:
        if key in row:
            return finite_number(row[key])
    return None


def _equity_points(
    path_value: str | os.PathLike[str] | None,
) -> tuple[list[tuple[datetime, float]], str, int]:
    rows, status, skipped = _load_jsonl(path_value)
    if status not in {"COMPLETE", "EMPTY"}:
        return [], status, skipped
    points: list[tuple[datetime, float]] = []
    invalid = 0
    for row in rows:
        if "equity" not in row:
            continue
        stamp = parse_timestamp(row.get("timestamp") or row.get("recorded_at"))
        equity = finite_number(row.get("equity"))
        if stamp is None or equity is None or equity <= 0.0:
            invalid += 1
            continue
        points.append((stamp, equity))
    if invalid:
        return [], "INCOMPLETE_MEASUREMENTS", invalid
    if not points:
        return [], "NO_MEASURABLE_POINTS", 0
    points.sort(key=lambda pair: pair[0])
    return points, "COMPLETE", 0


def _equity_metrics(
    points: list[tuple[datetime, float]],
    start: datetime,
    end: datetime,
) -> dict[str, Any]:
    period_points = [(stamp, value) for stamp, value in points if start <= stamp < end]
    prior_points = [(stamp, value) for stamp, value in points if stamp <= start]
    ending_points = [(stamp, value) for stamp, value in points if stamp < end]

    starting_equity = None
    start_timestamp = None
    if prior_points:
        stamp, value = prior_points[-1]
        if start - stamp <= _EQUITY_BOUNDARY_TOLERANCE:
            starting_equity, start_timestamp = value, stamp

    ending_equity = None
    end_timestamp = None
    if ending_points:
        stamp, value = ending_points[-1]
        if end - stamp <= _EQUITY_BOUNDARY_TOLERANCE:
            ending_equity, end_timestamp = value, stamp

    period_return = None
    if starting_equity is not None and ending_equity is not None and starting_equity > 0:
        period_return = (ending_equity / starting_equity - 1.0) * 100.0

    observed_values = [value for _, value in period_points]
    max_observed_drawdown = None
    if len(observed_values) >= 2:
        peak = observed_values[0]
        worst = 0.0
        for value in observed_values[1:]:
            peak = max(peak, value)
            worst = max(worst, (peak - value) / peak * 100.0)
        max_observed_drawdown = worst

    return {
        "starting_equity": round(starting_equity, 8) if starting_equity is not None else None,
        "ending_equity": round(ending_equity, 8) if ending_equity is not None else None,
        "period_return_pct": round(period_return, 6) if period_return is not None else None,
        "max_observed_drawdown_pct": round(max_observed_drawdown, 6) if max_observed_drawdown is not None else None,
        "period_equity_samples": len(period_points),
        "starting_equity_timestamp": utc_iso(start_timestamp) if start_timestamp else None,
        "ending_equity_timestamp": utc_iso(end_timestamp) if end_timestamp else None,
    }


def compute_period_metrics(
    start: datetime,
    end: datetime,
    *,
    ledger_path: str | os.PathLike[str] | None = None,
    equity_path: str | os.PathLike[str] | None = None,
    mode: str | None = None,
) -> dict[str, Any]:
    """Compute period metrics from a complete, mode-correct trade ledger.

    The period is half-open [start, end), and all timestamps are normalized to
    UTC. Any malformed JSONL row or unmeasurable closed trade invalidates ledger
    aggregates for the period so that a partial subset cannot masquerade as a
    complete result. Equity-boundary returns are reported only when both
    boundary-adjacent observations are within the configured one-hour tolerance.
    """
    if start.tzinfo is None or end.tzinfo is None or start >= end:
        raise ValueError("start and end must be aware datetimes with start < end")
    start = start.astimezone(timezone.utc)
    end = end.astimezone(timezone.utc)

    paths = default_evidence_paths(mode)
    actual_ledger_path = os.fspath(ledger_path) if ledger_path is not None else paths["ledger_path"]
    actual_equity_path = os.fspath(equity_path) if equity_path is not None else paths["equity_path"]
    records, ledger_status, skipped = _load_jsonl(actual_ledger_path)

    closed_rows: list[tuple[datetime, float, dict[str, Any]]] = []
    invalid_closed = 0
    if ledger_status in {"COMPLETE", "EMPTY"}:
        for row in records:
            if not _is_closed_trade(row):
                continue
            stamp = _trade_timestamp(row)
            pnl = _trade_pnl(row)
            if stamp is None or pnl is None:
                invalid_closed += 1
                continue
            if start <= stamp < end:
                closed_rows.append((stamp, pnl, row))
        if invalid_closed:
            ledger_status = "INCOMPLETE_CLOSED_TRADES"

    usable_ledger = ledger_status in {"COMPLETE", "EMPTY"}
    if usable_ledger:
        total = len(closed_rows)
        wins = [pnl for _, pnl, _ in closed_rows if pnl > 0.0]
        losses = [pnl for _, pnl, _ in closed_rows if pnl < 0.0]
        gross_profit = sum(wins)
        gross_loss = abs(sum(losses))
        trade_metrics: dict[str, Any] = {
            "closed_trade_count": total,
            "net_pnl_dollars": round(sum(pnl for _, pnl, _ in closed_rows), 8),
            "win_rate_pct": round(len(wins) / total * 100.0, 6) if total else None,
            "profit_factor": round(gross_profit / gross_loss, 6) if gross_loss > 0 else None,
            "measured_trade_rows": total,
        }
        grouped: dict[str, list[float]] = {}
        for _, pnl, row in closed_rows:
            name = row.get("strategy") or row.get("strategy_name") or row.get("strategy_id")
            if isinstance(name, str) and name.strip():
                grouped.setdefault(name.strip(), []).append(pnl)
        strategy_metrics: dict[str, Any] = {}
        for name, values in sorted(grouped.items()):
            group_wins = [pnl for pnl in values if pnl > 0.0]
            group_losses = [pnl for pnl in values if pnl < 0.0]
            gross_group_loss = abs(sum(group_losses))
            strategy_metrics[name] = {
                "closed_trade_count": len(values),
                "net_pnl_dollars": round(sum(values), 8),
                "win_rate_pct": round(len(group_wins) / len(values) * 100.0, 6) if values else None,
                "profit_factor": round(sum(group_wins) / gross_group_loss, 6) if gross_group_loss > 0 else None,
            }
        best = max(strategy_metrics.items(), key=lambda item: item[1]["net_pnl_dollars"], default=None)
        wins_ranked = [(pnl, row) for _, pnl, row in closed_rows if pnl > 0.0]
        losses_ranked = [(pnl, row) for _, pnl, row in closed_rows if pnl < 0.0]
        largest_win = _notable_trade(max(wins_ranked, key=lambda item: item[0])) if wins_ranked else None
        largest_loss = _notable_trade(min(losses_ranked, key=lambda item: item[0])) if losses_ranked else None
    else:
        trade_metrics = {
            "closed_trade_count": None,
            "net_pnl_dollars": None,
            "win_rate_pct": None,
            "profit_factor": None,
            "measured_trade_rows": None,
        }
        strategy_metrics = {}
        best = None
        largest_win = None
        largest_loss = None

    points, equity_status, equity_invalid = _equity_points(actual_equity_path)
    equity = _equity_metrics(points, start, end) if equity_status == "COMPLETE" else {
        "starting_equity": None,
        "ending_equity": None,
        "period_return_pct": None,
        "max_observed_drawdown_pct": None,
        "period_equity_samples": 0,
        "starting_equity_timestamp": None,
        "ending_equity_timestamp": None,
    }
    if not usable_ledger:
        evidence_status = "UNAVAILABLE" if ledger_status in {"MISSING", "UNREADABLE", "UNSUPPORTED_MODE"} else "INCOMPLETE"
    elif ledger_status == "EMPTY":
        evidence_status = "NO_LEDGER_ROWS"
    elif trade_metrics["closed_trade_count"] == 0:
        evidence_status = "NO_CLOSED_TRADES_IN_PERIOD"
    else:
        evidence_status = "MEASURED"

    return {
        "trading_mode": paths["trading_mode"],
        "evidence_status": evidence_status,
        "period_start_utc": utc_iso(start),
        "period_end_utc": utc_iso(end),
        "ledger_file": Path(actual_ledger_path).name if actual_ledger_path else None,
        "ledger_status": ledger_status,
        "ledger_skipped_rows": skipped,
        "invalid_closed_trade_rows": invalid_closed,
        "equity_file": Path(actual_equity_path).name if actual_equity_path else None,
        "equity_status": equity_status,
        "invalid_equity_rows": equity_invalid,
        **trade_metrics,
        **equity,
        "strategy_breakdown": strategy_metrics,
        "best_strategy": best[0] if best else None,
        "largest_win": largest_win,
        "largest_loss": largest_loss,
    }


def _notable_trade(item: tuple[float, dict[str, Any]]) -> dict[str, Any]:
    pnl, row = item
    return {
        "symbol": row.get("symbol") if isinstance(row.get("symbol"), str) else None,
        "strategy": row.get("strategy") or row.get("strategy_name") or row.get("strategy_id"),
        "net_pnl_dollars": round(pnl, 8),
        "trade_id": row.get("trade_id") or row.get("order_id"),
        "closed_at": next((row.get(key) for key in _TIME_FIELDS if row.get(key) is not None), None),
    }


def utc_period_for_day(day: date) -> tuple[datetime, datetime]:
    """Return the UTC calendar-day boundaries for a date."""
    start = datetime.combine(day, time.min, tzinfo=timezone.utc)
    return start, start + timedelta(days=1)
