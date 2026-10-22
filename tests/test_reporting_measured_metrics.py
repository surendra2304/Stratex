"""Regression tests for period-bounded reports and trustworthy performance evidence."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from reporting.daily_report import DailyReportGenerator
from reporting.ledger_metrics import compute_period_metrics
from reporting.periodic_reports import PeriodicReportGenerator
from reporting.voice_summaries import generate_daily_voice_summary


def _write_jsonl(path, rows):
    path.write_text(
        "".join(json.dumps(row, allow_nan=False) + "\n" for row in rows),
        encoding="utf-8",
    )


def test_period_metrics_filter_closed_trades_by_half_open_utc_window(tmp_path):
    ledger = tmp_path / "trades.jsonl"
    _write_jsonl(ledger, [
        {
            "status": "CLOSED",
            "trade_id": "win",
            "symbol": "BTCUSDT",
            "strategy": "supertrend",
            "timestamp": "2026-08-31T00:00:00Z",
            "net_pnl": 20.0,
        },
        {
            "trade_id": "loss",
            "symbol": "ETHUSDT",
            "strategy": "scalper",
            "exit_timestamp": "2026-09-06T23:59:59Z",
            "net_pnl": -5.0,
        },
        {
            "status": "CLOSED",
            "timestamp": "2026-09-07T00:00:00Z",
            "net_pnl": 1000.0,
        },
        {"status": "OPEN", "timestamp": "2026-09-03T12:00:00Z", "net_pnl": 5000.0},
        {
            "status": "CLOSED",
            "census_synthetic": True,
            "timestamp": "2026-09-03T12:00:00Z",
            "net_pnl": 9000.0,
        },
    ])
    start = datetime(2026, 8, 31, tzinfo=timezone.utc)
    end = datetime(2026, 9, 7, tzinfo=timezone.utc)

    result = compute_period_metrics(
        start,
        end,
        ledger_path=ledger,
        equity_path=tmp_path / "missing-equity.jsonl",
        mode="TESTNET",
    )

    assert result["evidence_status"] == "MEASURED"
    assert result["closed_trade_count"] == 2
    assert result["net_pnl_dollars"] == 15.0
    assert result["win_rate_pct"] == 50.0
    assert result["profit_factor"] == 4.0
    assert result["strategy_breakdown"]["supertrend"]["net_pnl_dollars"] == 20.0
    assert result["strategy_breakdown"]["scalper"]["net_pnl_dollars"] == -5.0
    assert result["largest_win"]["trade_id"] == "win"
    assert result["largest_loss"]["trade_id"] == "loss"
    assert result["period_equity_samples"] == 0


def test_missing_or_corrupt_ledger_never_reports_partial_aggregates(tmp_path):
    start = datetime(2026, 8, 31, tzinfo=timezone.utc)
    end = datetime(2026, 9, 7, tzinfo=timezone.utc)
    missing = compute_period_metrics(
        start, end, ledger_path=tmp_path / "missing.jsonl",
        equity_path=tmp_path / "missing-equity.jsonl", mode="TESTNET",
    )
    assert missing["evidence_status"] == "UNAVAILABLE"
    assert missing["closed_trade_count"] is None
    assert missing["net_pnl_dollars"] is None

    ledger = tmp_path / "corrupt.jsonl"
    ledger.write_text(
        json.dumps({
            "status": "CLOSED",
            "timestamp": "2026-09-01T12:00:00Z",
            "net_pnl": 100.0,
        }) + "\n{not-json\n",
        encoding="utf-8",
    )
    corrupt = compute_period_metrics(
        start, end, ledger_path=ledger,
        equity_path=tmp_path / "missing-equity.jsonl", mode="TESTNET",
    )
    assert corrupt["evidence_status"] == "INCOMPLETE"
    assert corrupt["ledger_status"] == "INCOMPLETE"
    assert corrupt["closed_trade_count"] is None
    assert corrupt["net_pnl_dollars"] is None


def test_equity_return_requires_near_boundary_samples_and_drawdown_is_observed(tmp_path):
    start = datetime(2026, 8, 31, tzinfo=timezone.utc)
    end = datetime(2026, 9, 7, tzinfo=timezone.utc)
    ledger = tmp_path / "empty-trades.jsonl"
    ledger.write_text("", encoding="utf-8")
    equity = tmp_path / "equity.jsonl"
    _write_jsonl(equity, [
        {"timestamp": start.isoformat().replace("+00:00", "Z"), "equity": 10000.0},
        {"timestamp": "2026-09-03T12:00:00Z", "equity": 9000.0},
        {"timestamp": "2026-09-06T23:30:00Z", "equity": 11000.0},
    ])

    result = compute_period_metrics(start, end, ledger_path=ledger, equity_path=equity, mode="TESTNET")

    assert result["starting_equity"] == 10000.0
    assert result["ending_equity"] == 11000.0
    assert result["period_return_pct"] == 10.0
    assert result["max_observed_drawdown_pct"] == 10.0
    assert result["evidence_status"] == "NO_LEDGER_ROWS"


def test_daily_report_without_ledger_does_not_seed_profit_or_strategy(tmp_path, monkeypatch):
    monkeypatch.setenv("TRADING_MODE", "TESTNET")
    monkeypatch.setenv("TESTNET_LEDGER_FILE", str(tmp_path / "missing-trades.jsonl"))
    monkeypatch.setenv("TESTNET_EQUITY_HISTORY_FILE", str(tmp_path / "missing-equity.jsonl"))
    generator = DailyReportGenerator(reports_dir=str(tmp_path / "reports"))

    report = generator.generate_daily_report(date_str="2026-08-31")

    assert report["evidence_status"] == "UNAVAILABLE"
    assert report["financial_metrics"]["net_pnl_dollars"] is None
    assert report["financial_metrics"]["net_pnl_pct"] is None
    assert report["financial_metrics"]["trades_count"] is None
    assert report["financial_metrics"]["win_rate_pct"] is None
    assert report["financial_metrics"]["profit_factor"] is None
    assert report["strategy_breakdown"] == {}
    assert report["notable_trades"] == {"largest_win": None, "largest_loss": None}
    assert "unavailable" in report["voice_summary"].lower()
    assert report["advisory_attribution"]["net_alpha_contribution_pct"] is None
    markdown = (tmp_path / "reports" / "report_2026-08-31.md").read_text(encoding="utf-8")
    assert "Unavailable" in markdown
    assert "3.42%" not in markdown and "1.74" not in markdown


def test_daily_report_html_escapes_caller_supplied_strategy_name(tmp_path, monkeypatch):
    monkeypatch.setenv("TRADING_MODE", "TESTNET")
    monkeypatch.setenv("TESTNET_LEDGER_FILE", str(tmp_path / "missing-trades.jsonl"))
    monkeypatch.setenv("TESTNET_EQUITY_HISTORY_FILE", str(tmp_path / "missing-equity.jsonl"))
    generator = DailyReportGenerator(reports_dir=str(tmp_path / "reports"))
    generator.generate_daily_report(
        date_str="2026-08-31",
        net_pnl=1.25,
        trades_count=1,
        best_strategy="<script>alert(1)</script>",
    )

    rendered = (tmp_path / "reports" / "report_2026-08-31.html").read_text(encoding="utf-8")
    assert "<script>" not in rendered.lower()
    assert "&lt;script&gt;" in rendered.lower()


def test_periodic_reports_do_not_assert_unmeasured_profit_or_capital_tier(tmp_path, monkeypatch):
    monkeypatch.setenv("TRADING_MODE", "TESTNET")
    monkeypatch.setenv("TESTNET_LEDGER_FILE", str(tmp_path / "missing-trades.jsonl"))
    monkeypatch.setenv("TESTNET_EQUITY_HISTORY_FILE", str(tmp_path / "missing-equity.jsonl"))
    generator = PeriodicReportGenerator(base_reports_dir=str(tmp_path / "reports"))

    weekly = generator.generate_weekly_report("2026-W36")
    monthly = generator.generate_monthly_report("2026-09")

    assert weekly["evidence_status"] == "UNAVAILABLE"
    assert weekly["metrics"]["weekly_return_pct"] is None
    assert weekly["metrics"]["total_trades"] is None
    assert "unavailable" in weekly["voice_summary"].lower()
    assert monthly["evidence_status"] == "UNAVAILABLE"
    assert monthly["metrics"]["monthly_return_pct"] is None
    assert monthly["metrics"]["total_trades"] is None
    assert monthly["metrics"]["capital_tier_status"] == "NOT_ASSESSED"
    assert monthly["advisory_cumulative_alpha_pct"] is None
    assert "8.92" not in monthly["voice_summary"]



def test_periodic_report_rejects_invalid_calendar_tags(tmp_path, monkeypatch):
    monkeypatch.setenv("TRADING_MODE", "TESTNET")
    generator = PeriodicReportGenerator(base_reports_dir=str(tmp_path / "reports"))

    for week_tag in ("2026-W00", "2026-W99", "../2026-W36", "2026-W3"):
        try:
            generator.generate_weekly_report(week_tag)
        except ValueError:
            pass
        else:
            raise AssertionError(f"Invalid ISO week was accepted: {week_tag}")

    for month_tag in ("2026-00", "2026-13", "../2026-09", "2026-9"):
        try:
            generator.generate_monthly_report(month_tag)
        except ValueError:
            pass
        else:
            raise AssertionError(f"Invalid calendar month was accepted: {month_tag}")



def test_invalid_closed_trade_outside_period_does_not_poison_current_metrics(tmp_path):
    ledger = tmp_path / "trades.jsonl"
    start = datetime(2026, 9, 7, tzinfo=timezone.utc)
    end = datetime(2026, 9, 14, tzinfo=timezone.utc)
    _write_jsonl(ledger, [
        {"status": "CLOSED", "exit_timestamp": "2026-08-01T00:00:00Z", "net_pnl": None},
        {"status": "CLOSED", "exit_timestamp": "2026-09-10T00:00:00Z", "net_pnl": 5.0},
    ])

    result = compute_period_metrics(
        start,
        end,
        ledger_path=ledger,
        equity_path=tmp_path / "missing-equity.jsonl",
        mode="TESTNET",
    )

    assert result["evidence_status"] == "MEASURED"
    assert result["closed_trade_count"] == 1
    assert result["net_pnl_dollars"] == 5.0
    assert result["invalid_closed_trade_rows"] == 0


def test_current_period_reports_end_at_observation_time(tmp_path, monkeypatch):
    monkeypatch.setenv("TRADING_MODE", "TESTNET")
    monkeypatch.setenv("TESTNET_LEDGER_FILE", str(tmp_path / "missing-trades.jsonl"))
    monkeypatch.setenv("TESTNET_EQUITY_HISTORY_FILE", str(tmp_path / "missing-equity.jsonl"))
    now = datetime.now(timezone.utc)

    daily = DailyReportGenerator(reports_dir=str(tmp_path / "daily")).generate_daily_report()
    weekly = PeriodicReportGenerator(base_reports_dir=str(tmp_path / "periodic")).generate_weekly_report()
    monthly = PeriodicReportGenerator(base_reports_dir=str(tmp_path / "periodic")).generate_monthly_report()

    for result, period in (
        (daily["data_quality"], "daily"),
        (weekly, "weekly"),
        (monthly, "monthly"),
    ):
        observed_end = datetime.fromisoformat(result["period_end_utc"].replace("Z", "+00:00"))
        assert observed_end <= now + timedelta(seconds=2), period
        assert observed_end > datetime.fromisoformat(result["period_start_utc"].replace("Z", "+00:00")), period


def test_voice_summary_does_not_turn_no_trades_into_zero_pnl_performance():
    voice = generate_daily_voice_summary(
        net_pnl_pct=None,
        net_pnl_dollars=0.0,
        best_strategy=None,
        trades_count=0,
        risk_headroom_pct=None,
    )
    assert "unavailable" in voice.lower()
    assert "net profit of $0.00" not in voice.lower()
    assert "No single strategy could be confirmed" in voice
