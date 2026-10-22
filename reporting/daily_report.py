"""Daily report generation from measured, period-bounded trading evidence."""
from __future__ import annotations

import datetime
import html
import math
import os
from typing import Any

from atomic_io import atomic_write_json, atomic_write_text
from reporting.advisory_impact import AdvisoryImpactAnalyzer
from reporting.ledger_metrics import compute_period_metrics, utc_period_for_day
from reporting.voice_summaries import generate_daily_voice_summary


def _optional_finite(value: Any, name: str) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be a finite number or None")
    number = float(value)
    if not math.isfinite(number):
        raise ValueError(f"{name} must be a finite number or None")
    return number


def _money(value: float | None) -> str:
    return "Unavailable" if value is None else f"${value:,.2f}"


def _percent(value: float | None, *, signed: bool = False, decimals: int = 2) -> str:
    if value is None:
        return "Unavailable"
    spec = f"+.{decimals}f" if signed else f".{decimals}f"
    return format(value, spec) + "%"


class DailyReportGenerator:
    """Generate JSON, Markdown, and HTML reports without seeding fake returns."""

    def __init__(self, reports_dir: str = "reports/daily"):
        self.reports_dir = reports_dir
        os.makedirs(self.reports_dir, exist_ok=True)
        self.impact_analyzer = AdvisoryImpactAnalyzer()

    def generate_daily_report(
        self,
        date_str: str | None = None,
        net_pnl: float | None = None,
        net_pnl_pct: float | None = None,
        starting_equity: float | None = None,
        ending_equity: float | None = None,
        trades_count: int | None = None,
        win_rate: float | None = None,
        max_drawdown_pct: float | None = None,
        best_strategy: str | None = None,
    ) -> dict[str, Any]:
        """Generate one UTC day of metrics from the active mode's ledger.

        Optional numeric arguments remain for existing internal callers. Every
        explicit override is validated and identified in the report metadata;
        omitted values come only from the configured mode-specific ledger and
        equity history. Missing, corrupt, or ambiguous input is reported as
        unavailable instead of being replaced by example performance numbers.
        """
        now = datetime.datetime.now(datetime.timezone.utc)
        target_date = date_str or now.date().isoformat()
        if not isinstance(target_date, str) or len(target_date) != 10:
            raise ValueError(f"report date must be YYYY-MM-DD, got {target_date!r}")
        target_day = datetime.date.fromisoformat(target_date)
        start, calendar_end = utc_period_for_day(target_day)
        # A report for the current UTC day covers elapsed time through the
        # latest report instant; completed historical days use their full day.
        end = min(calendar_end, now) if start <= now < calendar_end else calendar_end
        measured = compute_period_metrics(start, end)

        overrides: dict[str, Any] = {}
        for key, value in (
            ("net_pnl", net_pnl),
            ("net_pnl_pct", net_pnl_pct),
            ("starting_equity", starting_equity),
            ("ending_equity", ending_equity),
            ("win_rate", win_rate),
            ("max_drawdown_pct", max_drawdown_pct),
        ):
            validated = _optional_finite(value, key)
            if validated is not None:
                overrides[key] = validated
        if trades_count is not None:
            if isinstance(trades_count, bool) or not isinstance(trades_count, int) or trades_count < 0:
                raise ValueError("trades_count must be a non-negative integer or None")
            overrides["trades_count"] = trades_count
        if best_strategy is not None:
            if not isinstance(best_strategy, str) or not best_strategy.strip():
                raise ValueError("best_strategy must be a non-empty string or None")
            overrides["best_strategy"] = best_strategy.strip()

        values = {
            "starting_equity": measured["starting_equity"],
            "ending_equity": measured["ending_equity"],
            "net_pnl_dollars": measured["net_pnl_dollars"],
            "net_pnl_pct": measured["period_return_pct"],
            "trades_count": measured["closed_trade_count"],
            "win_rate_pct": measured["win_rate_pct"],
            "profit_factor": measured["profit_factor"],
            "max_drawdown_pct": measured["max_observed_drawdown_pct"],
            "best_strategy": measured["best_strategy"],
        }
        for key, value in overrides.items():
            mapped_key = {
                "net_pnl": "net_pnl_dollars",
                "win_rate": "win_rate_pct",
            }.get(key, key)
            values[mapped_key] = value

        if overrides and measured["evidence_status"] in {"MEASURED", "NO_CLOSED_TRADES_IN_PERIOD"}:
            evidence_status = "MEASURED_WITH_CALLER_OVERRIDES"
        elif overrides:
            evidence_status = "CALLER_SUPPLIED_OVERRIDES"
        else:
            evidence_status = measured["evidence_status"]

        risk_headroom = None
        voice_summary = generate_daily_voice_summary(
            net_pnl_pct=values["net_pnl_pct"],
            net_pnl_dollars=values["net_pnl_dollars"],
            best_strategy=values["best_strategy"],
            trades_count=values["trades_count"],
            risk_headroom_pct=risk_headroom,
        )

        summary_parts: list[str] = []
        if values["net_pnl_dollars"] is not None:
            summary_parts.append(f"Net realized PnL {_money(values['net_pnl_dollars'])}")
        if values["net_pnl_pct"] is not None:
            summary_parts.append(f"observed equity return {_percent(values['net_pnl_pct'], signed=True)}")
        if values["trades_count"] is not None:
            summary_parts.append(f"{values['trades_count']} measured closed trades")
        if values["win_rate_pct"] is not None:
            summary_parts.append(f"win rate {_percent(values['win_rate_pct'])}")
        executive_summary = (
            "; ".join(summary_parts) + "."
            if summary_parts
            else "Performance metrics are unavailable because complete period-matched evidence was not available."
        )

        advisory_summary = self.impact_analyzer.get_monthly_advisory_attribution_summary()
        report_payload = {
            "report_type": "DAILY_PERFORMANCE_REVIEW",
            "report_date": target_date,
            "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z"),
            "voice_summary": voice_summary,
            "executive_summary": executive_summary,
            "evidence_status": evidence_status,
            "financial_metrics": {
                "starting_equity": values["starting_equity"],
                "ending_equity": values["ending_equity"],
                "net_pnl_dollars": values["net_pnl_dollars"],
                "net_pnl_pct": values["net_pnl_pct"],
                "trades_count": values["trades_count"],
                "win_rate_pct": values["win_rate_pct"],
                "profit_factor": values["profit_factor"],
            },
            "risk_metrics": {
                "max_drawdown_pct": values["max_drawdown_pct"],
                "max_drawdown_measurement": (
                    "OBSERVED_EQUITY_SAMPLES"
                    if values["max_drawdown_pct"] is not None
                    else "UNAVAILABLE"
                ),
                "max_drawdown_limit_pct": None,
                "daily_loss_pct": None,
                "daily_loss_limit_pct": None,
                "risk_budget_remaining_pct": None,
            },
            "strategy_breakdown": measured["strategy_breakdown"],
            "advisory_attribution": advisory_summary,
            "notable_trades": {
                "largest_win": measured["largest_win"],
                "largest_loss": measured["largest_loss"],
            },
            "data_quality": {
                "period_start_utc": measured["period_start_utc"],
                "period_end_utc": measured["period_end_utc"],
                "trading_mode": measured["trading_mode"],
                "ledger_file": measured["ledger_file"],
                "ledger_status": measured["ledger_status"],
                "ledger_skipped_rows": measured["ledger_skipped_rows"],
                "invalid_closed_trade_rows": measured["invalid_closed_trade_rows"],
                "equity_file": measured["equity_file"],
                "equity_status": measured["equity_status"],
                "invalid_equity_rows": measured["invalid_equity_rows"],
                "period_equity_samples": measured["period_equity_samples"],
                "caller_provided_fields": sorted(overrides),
                "complete_ledger_required": True,
            },
        }

        json_path = os.path.join(self.reports_dir, f"report_{target_date}.json")
        md_path = os.path.join(self.reports_dir, f"report_{target_date}.md")
        html_path = os.path.join(self.reports_dir, f"report_{target_date}.html")
        atomic_write_json(json_path, report_payload, indent=2, allow_nan=False)
        atomic_write_text(md_path, self._render_markdown(report_payload))
        atomic_write_text(html_path, self._render_html(report_payload))
        return report_payload

    def _render_markdown(self, data: dict[str, Any]) -> str:
        financial = data["financial_metrics"]
        risk = data["risk_metrics"]
        advisory = data["advisory_attribution"]
        strategies = data.get("strategy_breakdown") or {}
        strategy_lines = [
            f"- **{name}**: {item['closed_trade_count']} closed trades, "
            f"{_money(item['net_pnl_dollars'])} net PnL, "
            f"{_percent(item['win_rate_pct'])} win rate"
            for name, item in sorted(strategies.items())
        ] or ["- No per-strategy closed-trade evidence was available."]
        return (
            f"# Daily Performance Report — {data['report_date']}\n\n"
            f"**Evidence status:** {data['evidence_status']}\n\n"
            f"**Voice Summary (FRIDAY):** {data['voice_summary']}\n\n"
            f"## Executive Summary\n{data['executive_summary']}\n\n"
            "## Financial & Risk Overview\n"
            f"- **Starting Equity:** {_money(financial['starting_equity'])}\n"
            f"- **Ending Equity:** {_money(financial['ending_equity'])}\n"
            f"- **Realized Net PnL:** {_money(financial['net_pnl_dollars'])}\n"
            f"- **Observed Equity Return:** {_percent(financial['net_pnl_pct'], signed=True)}\n"
            f"- **Closed Trades:** {financial['trades_count'] if financial['trades_count'] is not None else 'Unavailable'}\n"
            f"- **Win Rate:** {_percent(financial['win_rate_pct'])}\n"
            f"- **Profit Factor:** {financial['profit_factor'] if financial['profit_factor'] is not None else 'Unavailable'}\n"
            f"- **Maximum Observed Drawdown:** {_percent(risk['max_drawdown_pct'])}\n"
            f"- **Risk Headroom:** Unavailable unless an authoritative risk snapshot is configured.\n\n"
            "## Strategy Breakdown\n" + "\n".join(strategy_lines) + "\n\n"
            "## AI Advisory Impact\n"
            f"- Evidence status: {advisory.get('evidence_status', 'UNAVAILABLE')}\n"
            f"- Measured net alpha contribution: {_percent(advisory.get('net_alpha_contribution_pct'))}\n"
        )

    def _render_html(self, data: dict[str, Any]) -> str:
        financial = data["financial_metrics"]
        risk = data["risk_metrics"]
        advisory = data["advisory_attribution"]
        esc = lambda value: html.escape(str(value), quote=True)
        return f"""<!DOCTYPE html>
<html lang="en">
<head><meta charset="utf-8"><title>Daily Trading Report - {esc(data['report_date'])}</title></head>
<body style="font-family:sans-serif;padding:20px;background:#0f172a;color:#f8fafc;">
  <h2>Daily Performance Report — {esc(data['report_date'])}</h2>
  <p><strong>Evidence status:</strong> {esc(data['evidence_status'])}</p>
  <div style="background:#1e293b;padding:15px;border-radius:8px;border-left:4px solid #3b82f6;">
    <strong>FRIDAY Voice Summary:</strong> {esc(data['voice_summary'])}
  </div>
  <h3>Executive Summary</h3><p>{esc(data['executive_summary'])}</p>
  <h3>Financial Performance</h3>
  <p>Starting equity: <strong>{esc(_money(financial['starting_equity']))}</strong></p>
  <p>Ending equity: <strong>{esc(_money(financial['ending_equity']))}</strong></p>
  <p>Realized net PnL: <strong>{esc(_money(financial['net_pnl_dollars']))}</strong></p>
  <p>Observed equity return: <strong>{esc(_percent(financial['net_pnl_pct'], signed=True))}</strong></p>
  <p>Win rate: <strong>{esc(_percent(financial['win_rate_pct']))}</strong> across {esc(financial['trades_count'] if financial['trades_count'] is not None else 'unavailable')} measured closed trades.</p>
  <p>Profit factor: <strong>{esc(financial['profit_factor'] if financial['profit_factor'] is not None else 'Unavailable')}</strong></p>
  <p>Maximum observed drawdown: <strong>{esc(_percent(risk['max_drawdown_pct']))}</strong></p>
  <h3>AI Advisory Attribution</h3>
  <p>Evidence status: {esc(advisory.get('evidence_status', 'UNAVAILABLE'))}</p>
  <p>Measured net alpha contribution: <strong>{esc(_percent(advisory.get('net_alpha_contribution_pct')))}</strong></p>
</body>
</html>"""
