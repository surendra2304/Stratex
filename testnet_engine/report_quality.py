"""Evidence-based strategy quality report for paper/testnet ledger data.

This CLI intentionally distinguishes unavailable, incomplete, empty, and measured
inputs. It never seeds a starting balance or turns missing metrics into zero.
"""
from __future__ import annotations

import math
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

# Preserve direct script invocation (python testnet_engine/report_quality.py)
# as well as package execution (python -m testnet_engine.report_quality).
if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from typing import Any

from atomic_io import read_jsonl

OPPORTUNITY_LOG = os.getenv("TESTNET_OPPORTUNITY_LOG", "testnet_opportunity_log.jsonl")
LEDGER_LOG = os.getenv("TESTNET_LEDGER_FILE", "testnet_trade_ledger.jsonl")
EQUITY_HISTORY_LOG = os.getenv("TESTNET_EQUITY_HISTORY_FILE", "testnet_equity_history.jsonl")


def _finite_number(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) else None


def _load_records(path: str | os.PathLike[str] | None) -> tuple[list[dict[str, Any]], str, int]:
    if path is None:
        return [], "UNAVAILABLE", 0
    source = Path(path)
    if not source.is_file():
        return [], "MISSING", 0
    try:
        result = read_jsonl(source, expected_type=dict)
    except OSError:
        return [], "UNREADABLE", 0
    if not result.clean:
        return result.records, "INCOMPLETE", result.skipped
    return result.records, "COMPLETE" if result.records else "EMPTY", 0


def _is_synthetic(row: dict[str, Any]) -> bool:
    return row.get("census_synthetic") is True or row.get("synthetic") is True


def _is_closed_trade(row: dict[str, Any]) -> bool:
    if _is_synthetic(row):
        return False
    status = row.get("status")
    status = status.strip().upper() if isinstance(status, str) else ""
    close_fields = ("closed_at", "exit_timestamp", "exit_time", "close_timestamp")
    action = row.get("action")
    action_is_close = isinstance(action, str) and "CLOSE" in action.upper()
    return (
        status in {"CLOSED", "EXIT_FILLED", "CLOSE_FILLED", "CLOSED_FILLED"}
        or any(row.get(key) is not None for key in close_fields)
        or action_is_close
    )


def _row_timestamp(row: dict[str, Any]) -> datetime | None:
    for key in ("closed_at", "exit_timestamp", "exit_time", "close_timestamp", "timestamp"):
        value = row.get(key)
        try:
            if isinstance(value, bool) or value is None:
                continue
            if isinstance(value, (int, float)):
                stamp = _finite_number(value)
                if stamp is None:
                    continue
                if abs(stamp) >= 100_000_000_000:
                    stamp /= 1000.0
                parsed = datetime.fromtimestamp(stamp, timezone.utc)
            elif isinstance(value, str) and value.strip():
                parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
                if parsed.tzinfo is None:
                    parsed = parsed.replace(tzinfo=timezone.utc)
                parsed = parsed.astimezone(timezone.utc)
            else:
                continue
        except (ValueError, OverflowError, OSError):
            continue
        return parsed
    return None


def _closed_pnl(row: dict[str, Any]) -> float | None:
    for key in ("net_pnl", "pnl", "realized_pnl"):
        if key in row:
            return _finite_number(row[key])
    return None


def _aggregate_opportunities(path: str | os.PathLike[str] | None) -> dict[str, Any]:
    rows, source_status, skipped = _load_records(path)
    if source_status in {"MISSING", "UNAVAILABLE", "UNREADABLE", "INCOMPLETE"}:
        return {
            "evidence_status": source_status,
            "skipped_rows": skipped,
            "signals_evaluated": None,
            "signals_accepted": None,
            "signals_rejected": None,
            "accepted_without_return_metrics": None,
            "avg_expected_gross_return": None,
            "avg_expected_net_return": None,
        }

    accepted = [
        row for row in rows
        if isinstance(row.get("decision"), str)
        and (
            row["decision"].strip().upper() == "ACCEPTED"
            or row["decision"].strip().upper().startswith("ACCEPTED_")
            or row["decision"].strip().upper().startswith("ACCEPTED:")
        )
    ]
    rejected = len(rows) - len(accepted)
    gross_values: list[float] = []
    net_values: list[float] = []
    missing_metrics = 0
    for row in accepted:
        gross = _finite_number(row.get("expected_gross_return"))
        net = _finite_number(row.get("expected_net_return"))
        if gross is None or net is None:
            missing_metrics += 1
            continue
        gross_values.append(gross)
        net_values.append(net)

    return {
        "evidence_status": "NO_ROWS" if source_status == "EMPTY" else "MEASURED",
        "skipped_rows": 0,
        "signals_evaluated": len(rows),
        "signals_accepted": len(accepted),
        "signals_rejected": rejected,
        "accepted_without_return_metrics": missing_metrics,
        "avg_expected_gross_return": sum(gross_values) / len(gross_values) if gross_values else None,
        "avg_expected_net_return": sum(net_values) / len(net_values) if net_values else None,
    }


def _aggregate_closed_trades(path: str | os.PathLike[str] | None) -> dict[str, Any]:
    rows, source_status, skipped = _load_records(path)
    base = {
        "evidence_status": source_status,
        "skipped_rows": skipped,
        "closed_trade_count": None,
        "net_pnl": None,
        "avg_net_pnl_per_trade": None,
        "win_rate": None,
        "profit_factor": None,
        "gross_profit": None,
        "gross_loss": None,
        "fees_total": None,
        "fees_coverage": 0,
        "slippage_total": None,
        "slippage_coverage": 0,
    }
    if source_status in {"MISSING", "UNAVAILABLE", "UNREADABLE", "INCOMPLETE"}:
        return base

    closed = [row for row in rows if _is_closed_trade(row)]
    if not closed:
        base["evidence_status"] = "NO_CLOSED_TRADES" if source_status == "COMPLETE" else "NO_ROWS"
        base["closed_trade_count"] = 0
        return base

    pnls: list[float] = []
    fees: list[float] = []
    slippages: list[float] = []
    invalid_closed_rows = 0
    for row in closed:
        pnl = _closed_pnl(row)
        if pnl is None or _row_timestamp(row) is None:
            invalid_closed_rows += 1
            continue
        pnls.append(pnl)
        fee = _finite_number(row.get("fees"))
        slippage = _finite_number(row.get("slippage"))
        if fee is not None:
            fees.append(fee)
        if slippage is not None:
            slippages.append(slippage)

    if invalid_closed_rows or len(pnls) != len(closed):
        base["evidence_status"] = "INCOMPLETE_CLOSED_TRADES"
        base["invalid_closed_trade_rows"] = invalid_closed_rows
        return base

    winners = [pnl for pnl in pnls if pnl > 0.0]
    losers = [pnl for pnl in pnls if pnl < 0.0]
    gross_profit = sum(winners)
    gross_loss = abs(sum(losers))
    base.update({
        "evidence_status": "MEASURED",
        "closed_trade_count": len(pnls),
        "net_pnl": sum(pnls),
        "avg_net_pnl_per_trade": sum(pnls) / len(pnls),
        "win_rate": len(winners) / len(pnls),
        "profit_factor": gross_profit / gross_loss if gross_loss > 0.0 else None,
        "gross_profit": gross_profit,
        "gross_loss": gross_loss,
        "fees_total": sum(fees) if fees else None,
        "fees_coverage": len(fees),
        "slippage_total": sum(slippages) if slippages else None,
        "slippage_coverage": len(slippages),
    })
    return base


def _aggregate_equity(path: str | os.PathLike[str] | None) -> dict[str, Any]:
    rows, source_status, skipped = _load_records(path)
    result = {
        "evidence_status": source_status,
        "skipped_rows": skipped,
        "current_equity": None,
        "max_observed_drawdown_pct": None,
        "equity_samples": None if source_status in {"MISSING", "UNAVAILABLE", "UNREADABLE", "INCOMPLETE"} else len(rows),
    }
    if source_status in {"MISSING", "UNAVAILABLE", "UNREADABLE", "INCOMPLETE"}:
        return result

    points: list[tuple[datetime, float]] = []
    invalid = 0
    for row in rows:
        if "equity" not in row:
            continue
        timestamp = _row_timestamp(row)
        equity = _finite_number(row.get("equity"))
        if timestamp is None or equity is None or equity <= 0.0:
            invalid += 1
            continue
        points.append((timestamp, equity))
    if invalid:
        result["evidence_status"] = "INCOMPLETE_MEASUREMENTS"
        result["invalid_equity_rows"] = invalid
        result["current_equity"] = None
        result["max_observed_drawdown_pct"] = None
        return result
    points.sort(key=lambda point: point[0])
    if not points:
        result["evidence_status"] = "NO_EQUITY_SAMPLES"
        return result

    current_equity = points[-1][1]
    peak = points[0][1]
    max_drawdown = 0.0
    for _, equity in points:
        peak = max(peak, equity)
        if peak > 0:
            max_drawdown = max(max_drawdown, (peak - equity) / peak * 100.0)
    result.update({
        "evidence_status": "MEASURED",
        "current_equity": current_equity,
        "max_observed_drawdown_pct": max_drawdown if len(points) >= 2 else None,
        "equity_samples": len(points),
    })
    return result


def build_report(
    opportunity_log: str | os.PathLike[str] | None = None,
    ledger_log: str | os.PathLike[str] | None = None,
    equity_history_log: str | os.PathLike[str] | None = None,
) -> dict[str, Any]:
    """Build a JSON-serializable quality report from the selected evidence paths."""
    mode = os.getenv("TRADING_MODE", "TESTNET").strip().upper()
    opportunity_path = OPPORTUNITY_LOG if opportunity_log is None else opportunity_log
    ledger_path = LEDGER_LOG if ledger_log is None else ledger_log
    equity_path = EQUITY_HISTORY_LOG if equity_history_log is None else equity_history_log
    if mode not in {"TESTNET", "FUTURES"}:
        # This report is explicitly a testnet report. Do not label data from a
        # different execution environment as testnet or accidentally reuse a
        # testnet file when the process is configured for another mode.
        opportunities = _aggregate_opportunities(None)
        trades = _aggregate_closed_trades(None)
        equity = _aggregate_equity(None)
        opportunities["evidence_status"] = "UNSUPPORTED_MODE"
        trades["evidence_status"] = "UNSUPPORTED_MODE"
        equity["evidence_status"] = "UNSUPPORTED_MODE"
    else:
        opportunities = _aggregate_opportunities(opportunity_path)
        trades = _aggregate_closed_trades(ledger_path)
        equity = _aggregate_equity(equity_path)
    return {
        "report_type": "TESTNET_STRATEGY_QUALITY_CONTROL",
        "timestamp": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "trading_mode": mode,
        "profitability_guaranteed": False,
        "live_trading_enabled": False,
        "opportunities": opportunities,
        "closed_trades": trades,
        "equity": equity,
    }


def _display(value: Any, spec: str = "") -> str:
    if value is None:
        return "Unavailable"
    if spec:
        return format(value, spec)
    return str(value)


def render_report(report: dict[str, Any]) -> str:
    """Render measurements together with their evidence statuses."""
    opportunities = report["opportunities"]
    trades = report["closed_trades"]
    equity = report["equity"]
    lines = [
        "========================================",
        "TESTNET STRATEGY QUALITY CONTROL REPORT",
        "========================================",
        f"Opportunity evidence: {opportunities['evidence_status']}",
        f"Signals evaluated: {_display(opportunities['signals_evaluated'])}",
        f"Signals accepted:  {_display(opportunities['signals_accepted'])}",
        f"Signals rejected:  {_display(opportunities['signals_rejected'])}",
        f"Average expected gross return: {_display(opportunities['avg_expected_gross_return'], '.4%')}",
        f"Average expected net return:   {_display(opportunities['avg_expected_net_return'], '.4%')}",
        "----------------------------------------",
        f"Trade-ledger evidence: {trades['evidence_status']}",
        f"Closed trades: {_display(trades['closed_trade_count'])}",
        f"Net PnL: {_display(trades['net_pnl'], '.2f')} USDT",
        f"Average net PnL per trade: {_display(trades['avg_net_pnl_per_trade'], '.2f')} USDT",
        f"Win rate: {_display(trades['win_rate'], '.2%')}",
        f"Profit factor: {_display(trades['profit_factor'], '.2f')}",
        f"Fees observed: {_display(trades['fees_total'], '.2f')} USDT (coverage {trades['fees_coverage']})",
        f"Slippage observed: {_display(trades['slippage_total'], '.2f')} USDT (coverage {trades['slippage_coverage']})",
        "----------------------------------------",
        f"Equity evidence: {equity['evidence_status']}",
        f"Current equity: {_display(equity['current_equity'], '.2f')}",
        f"Maximum observed drawdown: {_display(equity['max_observed_drawdown_pct'], '.2f')}%",
        "========================================",
        "Profitability is not guaranteed.",
        "Live trading remains blocked.",
        "========================================",
    ]
    return "\n".join(lines)


def generate_report() -> dict[str, Any]:
    """CLI-compatible report generation, returning the payload for callers/tests."""
    report = build_report()
    print(render_report(report))
    return report


if __name__ == "__main__":
    generate_report()
