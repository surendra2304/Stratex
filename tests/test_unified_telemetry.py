"""
Comprehensive Test Suite for Unified Telemetry & Data Pipelines
Tests schema validation, atomic ledger persistence, query filters, metrics computation, and API endpoints.
"""

import json
import tempfile

import pytest

from dashboard import app
from testnet_engine.telemetry import (
    TelemetryManager,
    validate_balance_event,
    validate_equity_snapshot,
    validate_execution_event,
    validate_position_event,
    validate_signal_event,
    validate_trade_event,
)


@pytest.fixture
def temp_telemetry():
    with tempfile.TemporaryDirectory() as tmpdir:
        tm = TelemetryManager(base_dir=tmpdir)
        yield tm

def test_schema_validations():
    # Signal Event
    valid_sig = {
        "signal_id": "sig-001",
        "symbol": "BTCUSDT",
        "decision": "BUY",
        "strategy": "ADX_EMA",
        "timeframe": "5m",
        "entry": 50000.0,
        "stop": 49000.0,
        "target": 52000.0,
        "confidence": 0.85
    }
    validated_sig = validate_signal_event(valid_sig)
    assert validated_sig["signal_id"] == "sig-001"
    assert validated_sig["strategy"] == "ADX_EMA"
    assert "timestamp" in validated_sig

    # Execution Event
    valid_exec = {
        "event_type": "order_filled",
        "symbol": "ETHUSDT",
        "trade_id": "trade-001",
        "strategy": "ADX_EMA",
        "quantity": 0.5,
        "price": 3000.0
    }
    validated_exec = validate_execution_event(valid_exec)
    assert validated_exec["event_type"] == "order_filled"
    assert validated_exec["quantity"] == 0.5

    # Trade Event
    valid_trade = {
        "trade_id": "T-100",
        "symbol": "SOLUSDT",
        "strategy": "ADX_EMA",
        "timeframe": "15m",
        "side": "BUY",
        "status": "CLOSED",
        "entry_price": 100.0,
        "exit_price": 110.0,
        "quantity": 10.0,
        "gross_pnl": 100.0,
        "net_pnl": 95.0,
        "total_fees": 5.0
    }
    validated_trade = validate_trade_event(valid_trade)
    assert validated_trade["net_pnl"] == 95.0
    assert validated_trade["status"] == "CLOSED"

    # Position Event
    valid_pos = {
        "position_id": "BTCUSDT",
        "symbol": "BTCUSDT",
        "strategy": "ADX_EMA",
        "side": "BUY",
        "entry_price": 50000.0,
        "quantity": 0.1,
        "status": "OPEN"
    }
    validated_pos = validate_position_event(valid_pos)
    assert validated_pos["position_id"] == "BTCUSDT"

    # Balance Event
    valid_bal = {
        "event_type": "TRADE_CLOSE",
        "reason": "TAKE_PROFIT",
        "balance_before": 1000.0,
        "balance_after": 1095.0,
        "delta": 95.0
    }
    validated_bal = validate_balance_event(valid_bal)
    assert validated_bal["delta"] == 95.0

    # Equity Snapshot
    valid_eq = {
        "trigger_event": "INTERVAL_SNAPSHOT",
        "total_equity": 1095.0,
        "cash": 1095.0,
        "crypto_holdings_value": 0.0,
        "open_positions": 0
    }
    validated_eq = validate_equity_snapshot(valid_eq)
    assert validated_eq["total_equity"] == 1095.0

def test_telemetry_manager_recording_and_querying(temp_telemetry):
    tm = temp_telemetry

    # Record signals
    tm.record_signal_event({
        "signal_id": "sig-1",
        "symbol": "BTCUSDT",
        "strategy": "ADX_EMA",
        "timeframe": "5m",
        "decision": "BUY",
        "entry": 60000.0
    })
    tm.record_signal_event({
        "signal_id": "sig-2",
        "symbol": "ETHUSDT",
        "strategy": "ADX_EMA",
        "timeframe": "15m",
        "decision": "BUY",
        "entry": 3000.0
    })

    signals = tm.query_signals()
    assert len(signals) == 2
    btc_signals = tm.query_signals(symbol="BTCUSDT")
    assert len(btc_signals) == 1
    assert btc_signals[0]["symbol"] == "BTCUSDT"

    # Record trades
    tm.record_trade_event({
        "trade_id": "T-1",
        "symbol": "BTCUSDT",
        "strategy": "ADX_EMA",
        "timeframe": "5m",
        "side": "BUY",
        "status": "CLOSED",
        "entry_price": 60000.0,
        "exit_price": 61000.0,
        "quantity": 0.05,
        "gross_pnl": 50.0,
        "net_pnl": 45.0,
        "total_fees": 5.0
    })
    tm.record_trade_event({
        "trade_id": "T-2",
        "symbol": "ETHUSDT",
        "strategy": "ADX_EMA",
        "timeframe": "15m",
        "side": "BUY",
        "status": "CLOSED",
        "entry_price": 3000.0,
        "exit_price": 2900.0,
        "quantity": 1.0,
        "gross_pnl": -100.0,
        "net_pnl": -105.0,
        "total_fees": 5.0
    })

    closed_trades = tm.query_trades(status="CLOSED")
    assert len(closed_trades) == 2

    analytics = tm.compute_summary_analytics()
    assert analytics["total_trades"] == 2
    assert analytics["winning_trades"] == 1
    assert analytics["losing_trades"] == 1
    assert analytics["win_rate_pct"] == 50.0
    assert analytics["total_net_pnl"] == -60.0
    assert "by_strategy" in analytics
    assert "by_timeframe" in analytics
    assert "by_symbol" in analytics

def test_telemetry_position_state_transitions(temp_telemetry):
    tm = temp_telemetry

    # Open position
    tm.record_position_update({
        "position_id": "SOLUSDT",
        "symbol": "SOLUSDT",
        "strategy": "ADX_EMA",
        "side": "BUY",
        "entry_price": 150.0,
        "quantity": 2.0,
        "status": "OPEN"
    })
    open_pos = tm.query_positions(status="OPEN")
    assert len(open_pos) == 1
    assert open_pos[0]["symbol"] == "SOLUSDT"

    # Close position
    tm.record_position_update({
        "position_id": "SOLUSDT",
        "symbol": "SOLUSDT",
        "status": "CLOSED",
        "realized_pnl": 20.0
    })
    open_pos_after = tm.query_positions(status="OPEN")
    assert len(open_pos_after) == 0

    all_pos = tm.query_positions()
    assert len(all_pos) == 1
    assert all_pos[0]["status"] == "CLOSED"
    assert all_pos[0]["realized_pnl"] == 20.0

def test_telemetry_api_endpoints():
    client = app.test_client()

    resp = client.get('/api/telemetry/trades')
    assert resp.status_code == 200
    data = json.loads(resp.data)
    assert data["status"] == "OK"
    assert "trades" in data

    resp = client.get('/api/telemetry/signals')
    assert resp.status_code == 200
    data = json.loads(resp.data)
    assert data["status"] == "OK"
    assert "signals" in data

    resp = client.get('/api/telemetry/positions')
    assert resp.status_code == 200
    data = json.loads(resp.data)
    assert data["status"] == "OK"
    assert "positions" in data

    resp = client.get('/api/telemetry/equity_curve')
    assert resp.status_code == 200
    data = json.loads(resp.data)
    assert data["status"] == "OK"
    assert "equity_curve" in data

    resp = client.get('/api/telemetry/balance_events')
    assert resp.status_code == 200
    data = json.loads(resp.data)
    assert data["status"] == "OK"
    assert "balance_events" in data

    resp = client.get('/api/telemetry/analytics')
    assert resp.status_code == 200
    data = json.loads(resp.data)
    assert data["status"] == "OK"
    assert "analytics" in data
