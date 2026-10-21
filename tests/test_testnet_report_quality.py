"""Regression tests for the testnet quality report's evidence and safety semantics."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from testnet_engine.report_quality import build_report, render_report


def _write_jsonl(path, rows):
    path.write_text(
        "".join(json.dumps(row, allow_nan=False) + "\n" for row in rows),
        encoding="utf-8",
    )


def test_missing_evidence_is_unavailable_not_fabricated_zeroes(tmp_path, monkeypatch):
    monkeypatch.setenv("TRADING_MODE", "TESTNET")
    report = build_report(
        opportunity_log=tmp_path / "missing-opportunities.jsonl",
        ledger_log=tmp_path / "missing-trades.jsonl",
        equity_history_log=tmp_path / "missing-equity.jsonl",
    )

    assert report["opportunities"]["evidence_status"] == "MISSING"
    assert report["opportunities"]["signals_evaluated"] is None
    assert report["opportunities"]["avg_expected_net_return"] is None
    assert report["closed_trades"]["evidence_status"] == "MISSING"
    assert report["closed_trades"]["closed_trade_count"] is None
    assert report["closed_trades"]["net_pnl"] is None
    assert report["closed_trades"]["profit_factor"] is None
    assert report["equity"]["evidence_status"] == "MISSING"
    assert report["equity"]["current_equity"] is None
    assert report["equity"]["max_observed_drawdown_pct"] is None

    rendered = render_report(report)
    assert "Unavailable" in rendered
    assert "10,000.00" not in rendered
    assert "inf" not in rendered.lower()


def test_report_counts_closed_ledger_rows_and_uses_observed_equity(tmp_path, monkeypatch):
    monkeypatch.setenv("TRADING_MODE", "TESTNET")
    opportunities = tmp_path / "opportunities.jsonl"
    ledger = tmp_path / "trades.jsonl"
    equity = tmp_path / "equity.jsonl"
    _write_jsonl(opportunities, [
        {"decision": "ACCEPTED", "expected_gross_return": 0.04, "expected_net_return": 0.03},
        {"decision": "REJECTED", "expected_gross_return": 0.10, "expected_net_return": 0.01},
        {"decision": "ACCEPTED"},
        {"decision": "NOT_ACCEPTED", "expected_gross_return": 0.10, "expected_net_return": 0.01},
    ])
    _write_jsonl(ledger, [
        {
            "status": "CLOSED",
            "trade_id": "win",
            "symbol": "BTCUSDT",
            "timestamp": "2026-10-20T09:00:00Z",
            "net_pnl": 20.0,
            "fees": 0.50,
            "slippage": 0.25,
        },
        {
            "status": "CLOSED",
            "trade_id": "loss",
            "symbol": "ETHUSDT",
            "exit_timestamp": "2026-10-20T10:00:00Z",
            "net_pnl": -5.0,
            "fees": 0.25,
        },
        {"status": "OPEN", "timestamp": "2026-10-20T11:00:00Z", "net_pnl": 5000.0},
        {
            "status": "CLOSED",
            "census_synthetic": True,
            "timestamp": "2026-10-20T12:00:00Z",
            "net_pnl": 9000.0,
        },
    ])
    _write_jsonl(equity, [
        {"timestamp": "2026-10-20T08:00:00Z", "equity": 100.0},
        {"timestamp": "2026-10-20T09:00:00Z", "equity": 90.0},
        {"timestamp": "2026-10-20T10:00:00Z", "equity": 95.0},
    ])

    report = build_report(opportunities, ledger, equity)
    signals = report["opportunities"]
    trades = report["closed_trades"]
    observed_equity = report["equity"]

    assert signals["evidence_status"] == "MEASURED"
    assert signals["signals_evaluated"] == 4
    assert signals["signals_accepted"] == 2
    assert signals["signals_rejected"] == 2
    assert signals["accepted_without_return_metrics"] == 1
    assert signals["avg_expected_gross_return"] == 0.04
    assert signals["avg_expected_net_return"] == 0.03

    assert trades["evidence_status"] == "MEASURED"
    assert trades["closed_trade_count"] == 2
    assert trades["net_pnl"] == 15.0
    assert trades["avg_net_pnl_per_trade"] == 7.5
    assert trades["win_rate"] == 0.5
    assert trades["profit_factor"] == 4.0
    assert trades["fees_total"] == 0.75
    assert trades["fees_coverage"] == 2
    assert trades["slippage_total"] == 0.25
    assert trades["slippage_coverage"] == 1

    assert observed_equity["evidence_status"] == "MEASURED"
    assert observed_equity["current_equity"] == 95.0
    assert observed_equity["max_observed_drawdown_pct"] == 10.0
    assert observed_equity["equity_samples"] == 3


def test_corrupt_ledger_disables_pnl_aggregates_instead_of_reporting_partial_subset(tmp_path):
    ledger = tmp_path / "trades.jsonl"
    ledger.write_text(
        json.dumps({
            "status": "CLOSED",
            "trade_id": "visible-valid-row",
            "timestamp": "2026-10-20T09:00:00Z",
            "net_pnl": 100.0,
        }) + "\n{torn-json-fragment\n",
        encoding="utf-8",
    )
    report = build_report(
        opportunity_log=tmp_path / "missing-opportunities.jsonl",
        ledger_log=ledger,
        equity_history_log=tmp_path / "missing-equity.jsonl",
    )

    trades = report["closed_trades"]
    assert trades["evidence_status"] == "INCOMPLETE"
    assert trades["closed_trade_count"] is None
    assert trades["net_pnl"] is None
    assert trades["win_rate"] is None
    assert trades["profit_factor"] is None


def test_closed_trade_with_invalid_pnl_or_timestamp_is_not_silently_counted(tmp_path):
    ledger = tmp_path / "trades.jsonl"
    _write_jsonl(ledger, [
        {"status": "CLOSED", "timestamp": "2026-10-20T09:00:00Z", "net_pnl": 10.0},
        {"status": "CLOSED", "timestamp": "2026-10-20T10:00:00Z", "net_pnl": None},
    ])
    report = build_report(
        opportunity_log=tmp_path / "missing-opportunities.jsonl",
        ledger_log=ledger,
        equity_history_log=tmp_path / "missing-equity.jsonl",
    )

    assert report["closed_trades"]["evidence_status"] == "INCOMPLETE_CLOSED_TRADES"
    assert report["closed_trades"]["closed_trade_count"] is None
    assert report["closed_trades"]["net_pnl"] is None


def test_complete_empty_ledgers_report_zero_signal_or_closed_trade_counts(tmp_path):
    opportunities = tmp_path / "opportunities.jsonl"
    ledger = tmp_path / "trades.jsonl"
    equity = tmp_path / "equity.jsonl"
    for path in (opportunities, ledger, equity):
        path.write_text("", encoding="utf-8")

    report = build_report(opportunities, ledger, equity)

    assert report["opportunities"]["evidence_status"] == "NO_ROWS"
    assert report["opportunities"]["signals_evaluated"] == 0
    assert report["opportunities"]["signals_accepted"] == 0
    assert report["opportunities"]["avg_expected_gross_return"] is None
    assert report["closed_trades"]["evidence_status"] == "NO_ROWS"
    assert report["closed_trades"]["closed_trade_count"] == 0
    assert report["closed_trades"]["net_pnl"] is None
    assert report["equity"]["evidence_status"] == "NO_EQUITY_SAMPLES"
    assert report["equity"]["current_equity"] is None



def test_report_cli_runs_directly_from_a_different_working_directory(tmp_path):
    repository_root = Path(__file__).resolve().parents[1]
    script = repository_root / "testnet_engine" / "report_quality.py"
    environment = os.environ.copy()
    environment.update({
        "TRADING_MODE": "TESTNET",
        "TESTNET_OPPORTUNITY_LOG": str(tmp_path / "missing-opportunities.jsonl"),
        "TESTNET_LEDGER_FILE": str(tmp_path / "missing-trades.jsonl"),
        "TESTNET_EQUITY_HISTORY_FILE": str(tmp_path / "missing-equity.jsonl"),
    })

    process = subprocess.run(
        [sys.executable, str(script)],
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )

    assert process.returncode == 0, process.stderr
    assert "Opportunity evidence: MISSING" in process.stdout
    assert "Trade-ledger evidence: MISSING" in process.stdout
    assert "Current equity: Unavailable" in process.stdout



def test_testnet_quality_report_refuses_other_trading_modes(tmp_path, monkeypatch):
    monkeypatch.setenv("TRADING_MODE", "PAPER")
    opportunities = tmp_path / "opportunities.jsonl"
    ledger = tmp_path / "trades.jsonl"
    equity = tmp_path / "equity.jsonl"
    _write_jsonl(opportunities, [{"decision": "ACCEPTED", "expected_gross_return": 0.01, "expected_net_return": 0.01}])
    _write_jsonl(ledger, [{"status": "CLOSED", "timestamp": "2026-10-20T09:00:00Z", "net_pnl": 10.0}])
    _write_jsonl(equity, [{"timestamp": "2026-10-20T09:00:00Z", "equity": 100.0}])

    report = build_report(opportunities, ledger, equity)

    assert report["trading_mode"] == "PAPER"
    assert report["opportunities"]["evidence_status"] == "UNSUPPORTED_MODE"
    assert report["opportunities"]["signals_evaluated"] is None
    assert report["closed_trades"]["evidence_status"] == "UNSUPPORTED_MODE"
    assert report["closed_trades"]["net_pnl"] is None
    assert report["equity"]["evidence_status"] == "UNSUPPORTED_MODE"
    assert report["equity"]["current_equity"] is None
