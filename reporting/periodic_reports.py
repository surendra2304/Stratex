"""Weekly and monthly reports based on measured, period-bounded ledger evidence."""
from __future__ import annotations

import datetime
import re
from pathlib import Path
from typing import Any

from atomic_io import atomic_write_json
from reporting.ledger_metrics import compute_period_metrics, utc_iso


_WEEK_RE = re.compile(r"^(\d{4})-W(\d{2})$")
_MONTH_RE = re.compile(r"^(\d{4})-(\d{2})$")


def _money(value: float | None) -> str:
    return "Unavailable" if value is None else f"${value:,.2f}"


def _percent(value: float | None, *, signed: bool = False) -> str:
    if value is None:
        return "Unavailable"
    return format(value, "+.2f" if signed else ".2f") + "%"


class PeriodicReportGenerator:
    """Persist calendar-period rollups; do not manufacture profitability claims."""

    def __init__(self, base_reports_dir: str = "reports"):
        self.base_dir = base_reports_dir
        self.weekly_dir = str(Path(self.base_dir) / "weekly")
        self.monthly_dir = str(Path(self.base_dir) / "monthly")
        Path(self.weekly_dir).mkdir(parents=True, exist_ok=True)
        Path(self.monthly_dir).mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _weekly_bounds(week_tag: str) -> tuple[datetime.datetime, datetime.datetime]:
        match = _WEEK_RE.fullmatch(week_tag) if isinstance(week_tag, str) else None
        if match is None:
            raise ValueError("week_str must use ISO week format YYYY-Www")
        year, week = int(match.group(1)), int(match.group(2))
        try:
            monday = datetime.date.fromisocalendar(year, week, 1)
        except ValueError as exc:
            raise ValueError(f"invalid ISO week: {week_tag}") from exc
        start = datetime.datetime.combine(monday, datetime.time.min, tzinfo=datetime.timezone.utc)
        return start, start + datetime.timedelta(days=7)

    @staticmethod
    def _monthly_bounds(month_tag: str) -> tuple[datetime.datetime, datetime.datetime]:
        match = _MONTH_RE.fullmatch(month_tag) if isinstance(month_tag, str) else None
        if match is None:
            raise ValueError("month_str must use YYYY-MM format")
        year, month = int(match.group(1)), int(match.group(2))
        try:
            first = datetime.date(year, month, 1)
        except ValueError as exc:
            raise ValueError(f"invalid calendar month: {month_tag}") from exc
        following = datetime.date(year + (month == 12), 1 if month == 12 else month + 1, 1)
        start = datetime.datetime.combine(first, datetime.time.min, tzinfo=datetime.timezone.utc)
        end = datetime.datetime.combine(following, datetime.time.min, tzinfo=datetime.timezone.utc)
        return start, end

    @staticmethod
    def _voice_summary(period: str, label: str, metrics: dict[str, Any]) -> str:
        if metrics["evidence_status"] in {"MEASURED", "NO_CLOSED_TRADES_IN_PERIOD"}:
            parts = []
            if metrics["period_return_pct"] is not None:
                parts.append(f"observed equity return was {_percent(metrics['period_return_pct'], signed=True)}")
            if metrics["net_pnl_dollars"] is not None:
                parts.append(f"realized net PnL was {_money(metrics['net_pnl_dollars'])}")
            if metrics["closed_trade_count"] is not None:
                parts.append(f"{metrics['closed_trade_count']} closed trades were measured")
            if parts:
                equity_note = (
                    " Maximum observed drawdown was "
                    + _percent(metrics["max_observed_drawdown_pct"])
                    + "."
                    if metrics["max_observed_drawdown_pct"] is not None
                    else " Drawdown was not measurable from the available equity samples."
                )
                return f"{period} review for {label}: " + "; ".join(parts) + "." + equity_note
        return (
            f"{period} review for {label}: performance metrics are unavailable or incomplete. "
            "No return, win-rate, or risk status is asserted without complete ledger evidence."
        )

    def generate_weekly_report(self, week_str: str | None = None) -> dict[str, Any]:
        now = datetime.datetime.now(datetime.timezone.utc)
        week_tag = week_str or now.strftime("%G-W%V")
        start, calendar_end = self._weekly_bounds(week_tag)
        # For the current week, report the elapsed period through now instead
        # of comparing current equity to a boundary that is still in the future.
        end = min(calendar_end, now) if start <= now < calendar_end else calendar_end
        measured = compute_period_metrics(start, end)
        metrics = {
            "weekly_return_pct": measured["period_return_pct"],
            "total_trades": measured["closed_trade_count"],
            "overall_win_rate_pct": measured["win_rate_pct"],
            "profit_factor": measured["profit_factor"],
            "max_drawdown_pct": measured["max_observed_drawdown_pct"],
        }
        report = {
            "report_type": "WEEKLY_PERFORMANCE_REVIEW",
            "week": week_tag,
            "period_start_utc": utc_iso(start),
            "period_end_utc": utc_iso(end),
            "timestamp": utc_iso(now),
            "evidence_status": measured["evidence_status"],
            "voice_summary": self._voice_summary("Weekly", week_tag, measured),
            "metrics": metrics,
            "strategy_consistency": measured["strategy_breakdown"],
            "strategy_breakdown": measured["strategy_breakdown"],
            "data_quality": {
                "trading_mode": measured["trading_mode"],
                "ledger_file": measured["ledger_file"],
                "ledger_status": measured["ledger_status"],
                "ledger_skipped_rows": measured["ledger_skipped_rows"],
                "invalid_closed_trade_rows": measured["invalid_closed_trade_rows"],
                "equity_file": measured["equity_file"],
                "equity_status": measured["equity_status"],
                "period_equity_samples": measured["period_equity_samples"],
            },
        }
        path = str(Path(self.weekly_dir) / f"weekly_{week_tag}.json")
        atomic_write_json(path, report, indent=2, allow_nan=False)
        return report

    def generate_monthly_report(self, month_str: str | None = None) -> dict[str, Any]:
        now = datetime.datetime.now(datetime.timezone.utc)
        month_tag = month_str or now.strftime("%Y-%m")
        start, calendar_end = self._monthly_bounds(month_tag)
        # A current-month report uses evidence through now; historical months
        # retain their full calendar-month boundary.
        end = min(calendar_end, now) if start <= now < calendar_end else calendar_end
        measured = compute_period_metrics(start, end)
        metrics = {
            "monthly_return_pct": measured["period_return_pct"],
            "total_trades": measured["closed_trade_count"],
            "win_rate_pct": measured["win_rate_pct"],
            "profit_factor": measured["profit_factor"],
            "max_drawdown_pct": measured["max_observed_drawdown_pct"],
            "net_pnl_dollars": measured["net_pnl_dollars"],
            "capital_tier_status": "NOT_ASSESSED",
        }
        report = {
            "report_type": "MONTHLY_EXECUTIVE_AUDIT",
            "month": month_tag,
            "period_start_utc": utc_iso(start),
            "period_end_utc": utc_iso(end),
            "timestamp": utc_iso(now),
            "evidence_status": measured["evidence_status"],
            "voice_summary": self._voice_summary("Monthly", month_tag, measured),
            "metrics": metrics,
            "advisory_cumulative_alpha_pct": None,
            "advisory_evidence_status": "UNAVAILABLE_NO_MATCHED_OUTCOME_LEDGER",
            "strategy_breakdown": measured["strategy_breakdown"],
            "data_quality": {
                "trading_mode": measured["trading_mode"],
                "ledger_file": measured["ledger_file"],
                "ledger_status": measured["ledger_status"],
                "ledger_skipped_rows": measured["ledger_skipped_rows"],
                "invalid_closed_trade_rows": measured["invalid_closed_trade_rows"],
                "equity_file": measured["equity_file"],
                "equity_status": measured["equity_status"],
                "period_equity_samples": measured["period_equity_samples"],
            },
        }
        path = str(Path(self.monthly_dir) / f"monthly_{month_tag}.json")
        atomic_write_json(path, report, indent=2, allow_nan=False)
        return report
