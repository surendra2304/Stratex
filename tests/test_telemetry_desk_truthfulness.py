"""The operations desk must show trades that actually executed.

Boots the real Flask app against a real on-disk execution ledger, the same way the
service does in production, and asserts the desk's own endpoint reports them. These
fail when the endpoint falls back to the per-process canonical index, which is loaded
once at startup and cannot observe trades written by the engine process afterwards.
"""

import json
import os

import pytest


LEDGER_RECORDS = [
    {
        "symbol": "ATOMUSDT",
        "action": "LONG",
        "strategy": "SUPERTREND",
        "quantity": 53.59,
        "entry_price": 1.864,
        "exit_price": 1.863,
        "pnl": -0.0935,
        "fees": 0.0399,
        "status": "CLOSED",
        "order_id": "609738597",
        "timestamp": "2026-09-26T17:25:53.148000Z",
    },
    {
        "symbol": "LINKUSDT",
        "action": "SHORT",
        "strategy": "SUPERTREND",
        "quantity": 8.6,
        "entry_price": 11.571,
        "exit_price": 11.58,
        "pnl": 0.4122,
        "fees": 0.0398,
        "status": "CLOSED",
        "order_id": "1048979447",
        "timestamp": "2026-09-12T16:15:58.266000Z",
    },
]

SIGNAL_RECORDS = [
    {
        "signal_id": "af4fa60c-c4cd-44f9-8b9c-39a3ab3048c1",
        "timestamp": 1788881400.0,
        "strategy": "strategy_swing_macd_200ema",
        "symbol": "BTCUSDT",
        "side": "BUY",
        "confidence": 1.0,
        "entry_price": 78725.84,
        "decision": "REJECTED",
        "rejection_reason": "RISK_LIMIT: Max portfolio exposure exceeded",
        "data_source": "BINANCE_REST",
        "timeframe": "1h",
    }
]

DESK_REQUIRED_FIELDS = (
    "close_timestamp",
    "symbol",
    "side",
    "strategy",
    "quantity",
    "entry_price",
    "exit_price",
    "net_pnl",
    "total_fees",
)


@pytest.fixture()
def desk(tmp_path, monkeypatch):
    """Boots the real dashboard against a real ledger, with no live exchange calls."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "testnet_trade_ledger.jsonl").write_text(
        "".join(json.dumps(r) + "\n" for r in LEDGER_RECORDS), encoding="utf-8"
    )
    (tmp_path / "forward_signal_log.jsonl").write_text(
        "".join(json.dumps(r) + "\n" for r in SIGNAL_RECORDS), encoding="utf-8"
    )
    monkeypatch.setenv("TRADING_MODE", "TESTNET")
    # The ledger location is resolved from a config module captured at import time, so
    # point at the fixture ledger explicitly. This also suppresses the live Binance
    # reconciliation path, keeping the test hermetic.
    monkeypatch.setenv("TESTNET_LEDGER_FILE", str(tmp_path / "testnet_trade_ledger.jsonl"))
    monkeypatch.setenv("BINANCE_API_KEY", "")
    monkeypatch.setenv("BINANCE_API_SECRET", "")

    import dashboard

    application = dashboard.app
    application.config.update(TESTING=True)
    with application.test_client() as client:
        yield client


def test_closed_trades_are_served_from_the_execution_ledger(desk):
    response = desk.get("/api/telemetry/trades?status=CLOSED&limit=100")
    assert response.status_code == 200
    payload = response.get_json()
    symbols = {t["symbol"] for t in payload["trades"]}
    assert "ATOMUSDT" in symbols, "an executed trade must not be hidden from the desk"
    assert payload["count"] == len(LEDGER_RECORDS)


def test_ledger_field_names_are_mapped_to_what_the_desk_renders(desk):
    """The ledger writes action/pnl/fees; the desk reads side/net_pnl/total_fees."""
    trades = desk.get("/api/telemetry/trades?status=CLOSED").get_json()["trades"]
    atom = next(t for t in trades if t["symbol"] == "ATOMUSDT")
    assert atom["side"] == "LONG"
    assert atom["net_pnl"] == pytest.approx(-0.0935)
    assert atom["total_fees"] == pytest.approx(0.0399)
    assert atom["close_timestamp"].startswith("2026-09-26T17:25:53")


def test_every_rendered_row_has_the_fields_the_table_binds(desk):
    trades = desk.get("/api/telemetry/trades?status=CLOSED").get_json()["trades"]
    for trade in trades:
        for field in DESK_REQUIRED_FIELDS:
            assert trade.get(field) not in (None, ""), f"{trade.get('symbol')} is missing {field}"


def test_the_two_ledgers_agree_on_totals(desk):
    """A zero-trade table beside a non-zero P&L card is the exact lie this prevents."""
    telemetry = desk.get("/api/telemetry/trades?status=CLOSED").get_json()
    ledger_view = desk.get("/api/trades").get_json()
    telemetry_net = sum(t["net_pnl"] for t in telemetry["trades"])
    assert telemetry_net == pytest.approx(ledger_view["net_pnl"], abs=1e-6)
    assert telemetry["count"] == len(ledger_view["positions"])


def test_signal_funnel_comes_from_the_forward_runner_log(desk):
    payload = desk.get("/api/telemetry/signals?limit=10").get_json()
    assert payload["count"] == len(SIGNAL_RECORDS)
    signal = payload["signals"][0]
    assert signal["symbol"] == "BTCUSDT"
    assert signal["reason"] == "RISK_LIMIT: Max portfolio exposure exceeded"
    # Epoch timestamps must be converted; the desk formats this as a date.
    assert signal["timestamp"] == "2026-09-08T15:30:00Z"


def test_status_filter_does_not_leak_open_trades(desk):
    everything = desk.get("/api/telemetry/trades?limit=100").get_json()
    closed_only = desk.get("/api/telemetry/trades?status=CLOSED").get_json()
    assert all(t["status"] == "CLOSED" for t in closed_only["trades"])
    assert closed_only["count"] <= everything["count"]


def test_a_trade_absent_from_both_ledgers_is_reported_as_absent(desk, tmp_path):
    """Honest absence: the desk must not invent rows."""
    (tmp_path / "testnet_trade_ledger.jsonl").write_text("", encoding="utf-8")
    payload = desk.get("/api/telemetry/trades?status=CLOSED").get_json()
    assert payload["count"] == 0
    assert payload["trades"] == []
