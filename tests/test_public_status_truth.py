"""Persisted status API metrics never invent operational or trading results."""

import json
from datetime import datetime, timezone

import api.public_status as public_status


def test_missing_ledgers_return_unknown_metrics(monkeypatch, tmp_path):
    monkeypatch.setattr(public_status, "PROJECT_ROOT", tmp_path)

    metrics = public_status._closed_trade_metrics()
    assert metrics == {
        "trades": 0,
        "win_rate": None,
        "profit_factor": None,
        "net_pnl": None,
        "last_trade_timestamp": None,
    }


def test_metrics_use_only_closed_ledger_rows(monkeypatch, tmp_path):
    monkeypatch.setattr(public_status, "PROJECT_ROOT", tmp_path)
    ledger = tmp_path / "paper_trade_ledger.jsonl"
    rows = [
        {"status": "CLOSED", "net_pnl": 12.0, "closed_at": "2026-09-28T10:00:00Z"},
        {"status": "CLOSED", "net_pnl": -4.0, "closed_at": "2026-09-28T11:00:00Z"},
        {"status": "OPEN", "net_pnl": 1000.0},
    ]
    ledger.write_text("\n".join(json.dumps(row) for row in rows), encoding="utf-8")

    metrics = public_status._closed_trade_metrics()
    assert metrics["trades"] == 2
    assert metrics["win_rate"] == 50.0
    assert metrics["profit_factor"] == 3.0
    assert metrics["net_pnl"] == 8.0
    assert metrics["last_trade_timestamp"] == "2026-09-28T11:00:00Z"


def test_runner_activity_requires_recent_heartbeat(monkeypatch, tmp_path):
    monkeypatch.setattr(public_status, "PROJECT_ROOT", tmp_path)
    heartbeat = tmp_path / "paper_runner_heartbeat.json"
    heartbeat.write_text(json.dumps({
        "alive": True,
        "status": "RUNNING",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }), encoding="utf-8")
    _, fresh, age = public_status._fresh_paper_runner()
    assert fresh is True
    assert age is not None and age < public_status.HEARTBEAT_MAX_AGE_SECONDS

    heartbeat.write_text(json.dumps({
        "alive": True,
        "status": "RUNNING",
        "timestamp": "2020-01-01T00:00:00Z",
    }), encoding="utf-8")
    _, fresh, age = public_status._fresh_paper_runner()
    assert fresh is False
    assert age is not None and age > public_status.HEARTBEAT_MAX_AGE_SECONDS
