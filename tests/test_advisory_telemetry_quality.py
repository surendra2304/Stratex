"""Evidence-integrity regression tests for AI-advisory telemetry."""
from __future__ import annotations

import json
from datetime import datetime, timezone

from advisory_telemetry import build_telemetry_payload


def _write_jsonl(path, rows):
    path.write_text(
        "".join(json.dumps(row, allow_nan=False) + "\n" for row in rows),
        encoding="utf-8",
    )


def _closed_trade(index, pnl):
    return {
        "trade_id": f"closed-{index}",
        "status": "CLOSED",
        "symbol": "BTCUSDT",
        "timestamp": f"2026-10-20T00:{index:02d}:00Z",
        "net_pnl": pnl,
    }


def test_missing_sources_are_unavailable_and_block_advisory(monkeypatch, tmp_path):
    monkeypatch.setenv("TESTNET_PORTFOLIO_FILE", str(tmp_path / "missing-portfolio.json"))
    monkeypatch.setenv("TESTNET_LEDGER_FILE", str(tmp_path / "missing-ledger.jsonl"))

    payload = build_telemetry_payload(trading_mode="TESTNET")

    assert payload["telemetry_quality"]["advisory_ready"] is False
    assert payload["telemetry_quality"]["portfolio_status"] == "MISSING"
    assert payload["telemetry_quality"]["ledger_status"] == "MISSING"
    assert "fresh_portfolio_snapshot" in payload["telemetry_quality"]["blocking_reasons"]
    assert payload["telemetry"]["equity"] is None
    assert payload["telemetry"]["profit_factor"] is None
    assert payload["telemetry"]["total_trades"] is None
    assert payload["market_regime"] == {"regime": "UNKNOWN", "volatility_state": "UNKNOWN"}
    assert payload["telemetry_freshness"]["status"] == "UNKNOWN"
    assert payload["exchange_status"]["connectivity"] == "UNKNOWN"
    assert payload["positions"]["reconciliation_status"] == "UNKNOWN"
    assert payload["testnet_specific"] is None


def test_only_closed_measured_trades_contribute_and_complete_recent_data_can_be_ready(monkeypatch, tmp_path):
    portfolio = tmp_path / "portfolio.json"
    ledger = tmp_path / "ledger.jsonl"
    now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    portfolio.write_text(json.dumps({
        "equity": 10250.0,
        "cash": 10000.0,
        "realized_pnl": 250.0,
        "unrealized_pnl": 0.0,
        "max_drawdown_pct": 2.5,
        "positions": [],
        "timestamp": now,
    }), encoding="utf-8")
    trades = [_closed_trade(i, 5.0 if i % 2 == 0 else -2.0) for i in range(12)]
    trades.extend([
        {"trade_id": "open", "status": "OPEN", "timestamp": now, "net_pnl": 9999.0},
        {"trade_id": "census", "status": "CLOSED", "census_synthetic": True, "timestamp": now, "net_pnl": 9999.0},
    ])
    _write_jsonl(ledger, trades)
    monkeypatch.setenv("TESTNET_PORTFOLIO_FILE", str(portfolio))
    monkeypatch.setenv("TESTNET_LEDGER_FILE", str(ledger))

    payload = build_telemetry_payload(trading_mode="TESTNET")

    assert payload["telemetry_quality"]["portfolio_status"] == "MEASURED"
    assert payload["telemetry_quality"]["portfolio_freshness_status"] == "FRESH"
    assert payload["telemetry_quality"]["ledger_status"] == "COMPLETE"
    assert payload["telemetry_quality"]["closed_trade_count"] == 12
    assert payload["telemetry_quality"]["advisory_ready"] is True
    assert payload["telemetry"]["total_trades"] == 12
    assert payload["telemetry"]["win_rate"] == 0.5
    assert payload["telemetry"]["profit_factor"] > 0
    assert len(payload["recent_trades"]) == 12
    assert payload["exchange_status"]["connectivity"] == "UNKNOWN"


def test_corrupt_ledger_cannot_produce_partial_advisory_metrics(monkeypatch, tmp_path):
    portfolio = tmp_path / "portfolio.json"
    ledger = tmp_path / "ledger.jsonl"
    now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    portfolio.write_text(json.dumps({
        "equity": 10250.0,
        "cash": 10000.0,
        "realized_pnl": 250.0,
        "unrealized_pnl": 0.0,
        "max_drawdown_pct": 2.5,
        "positions": [],
        "timestamp": now,
    }), encoding="utf-8")
    ledger.write_text(
        json.dumps(_closed_trade(1, 10.0)) + "\n{truncated\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("TESTNET_PORTFOLIO_FILE", str(portfolio))
    monkeypatch.setenv("TESTNET_LEDGER_FILE", str(ledger))

    payload = build_telemetry_payload(trading_mode="TESTNET")

    assert payload["telemetry_quality"]["ledger_status"] == "INCOMPLETE"
    assert payload["telemetry_quality"]["advisory_ready"] is False
    assert payload["telemetry"]["total_trades"] is None
    assert payload["telemetry"]["win_rate"] is None
    assert payload["telemetry"]["profit_factor"] is None
    assert payload["recent_trades"] is None


def test_stale_portfolio_snapshot_is_not_consultable(monkeypatch, tmp_path):
    portfolio = tmp_path / "portfolio.json"
    ledger = tmp_path / "ledger.jsonl"
    portfolio.write_text(json.dumps({
        "equity": 10250.0,
        "cash": 10000.0,
        "realized_pnl": 250.0,
        "unrealized_pnl": 0.0,
        "max_drawdown_pct": 2.5,
        "positions": [],
        "timestamp": "2020-01-01T00:00:00Z",
    }), encoding="utf-8")
    _write_jsonl(ledger, [_closed_trade(i, 5.0 if i % 2 == 0 else -2.0) for i in range(12)])
    monkeypatch.setenv("TESTNET_PORTFOLIO_FILE", str(portfolio))
    monkeypatch.setenv("TESTNET_LEDGER_FILE", str(ledger))

    payload = build_telemetry_payload(trading_mode="TESTNET")

    assert payload["telemetry_quality"]["portfolio_freshness_status"] == "STALE"
    assert payload["telemetry_quality"]["advisory_ready"] is False
    assert "fresh_portfolio_snapshot" in payload["telemetry_quality"]["blocking_reasons"]
