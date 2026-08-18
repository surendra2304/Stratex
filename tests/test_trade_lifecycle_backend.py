"""
Regression tests for the Trade Lifecycle Backend.
Covers all 15 lifecycle questions, 3 new endpoints, equity accounting invariant,
and full TelemetryManager round-trip.
"""

import json
import tempfile
import datetime
import pytest
from dashboard import app
from testnet_engine.telemetry_manager import TelemetryManager


@pytest.fixture
def client():
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c


@pytest.fixture
def isolated_telemetry():
    with tempfile.TemporaryDirectory() as tmpdir:
        yield TelemetryManager(base_dir=tmpdir)


# ---------------------------------------------------------------------------
# 1. get_funnel() / /api/opportunities
# ---------------------------------------------------------------------------

class TestGetFunnel:
    def test_opportunities_returns_200(self, client):
        """get_funnel() must not raise NameError - /api/opportunities must be 200."""
        resp = client.get("/api/opportunities")
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.data[:300]}"

    def test_opportunities_response_structure(self, client):
        data = json.loads(client.get("/api/opportunities").data)
        assert "status" in data
        assert "top_opportunities" in data
        assert isinstance(data["top_opportunities"], list)
        assert "strategy_metrics" in data
        assert "timeframe_metrics" in data
        assert "timestamp" in data

    def test_opportunities_funnel_counters_present(self, client):
        data = json.loads(client.get("/api/opportunities").data)
        for key in ["TOTAL_SIGNALS", "PROFITABILITY_ACCEPTED", "PROFITABILITY_REJECTED",
                    "RISK_ACCEPTED", "RISK_REJECTED", "QUALIFIED", "ORDERS_SUBMITTED",
                    "ORDERS_FILLED"]:
            assert key in data, f"Missing funnel counter: {key}"
            assert isinstance(data[key], (int, float)), f"{key} should be numeric"


# ---------------------------------------------------------------------------
# 2. /api/risk-events
# ---------------------------------------------------------------------------

class TestRiskEvents:
    def test_risk_events_returns_200(self, client):
        assert client.get("/api/risk-events").status_code == 200

    def test_risk_events_structure(self, client):
        data = json.loads(client.get("/api/risk-events").data)
        assert data["status"] == "SUCCESS"
        assert "events" in data
        assert isinstance(data["events"], list)
        assert "count" in data
        assert "timestamp" in data

    def test_risk_events_limit_param(self, client):
        data = json.loads(client.get("/api/risk-events?limit=5").data)
        assert len(data["events"]) <= 5

    def test_risk_events_symbol_filter(self, client):
        data = json.loads(client.get("/api/risk-events?symbol=BTCUSDT").data)
        for ev in data["events"]:
            assert ev["symbol"] == "BTCUSDT"

    def test_risk_events_each_has_required_fields(self, client):
        data = json.loads(client.get("/api/risk-events").data)
        required = {"timestamp", "event_type", "symbol", "strategy", "reason", "decision", "source"}
        for ev in data["events"]:
            missing = required - set(ev.keys())
            assert not missing, f"Risk event missing fields: {missing}"

    def test_risk_events_from_telemetry(self, isolated_telemetry):
        isolated_telemetry.record_signal_event({
            "signal_id": "sig-risk-001",
            "symbol": "BTCUSDT",
            "strategy": "ADX_EMA",
            "timeframe": "5m",
            "decision": "BUY",
            "risk_decision": "REJECTED",
            "risk_reason": "MAX_EXPOSURE_EXCEEDED",
        })
        signals = isolated_telemetry.get_signals_log(limit=10)
        risk_rejected = [s for s in signals if s.get("risk_decision") == "REJECTED"]
        assert len(risk_rejected) == 1
        assert risk_rejected[0]["risk_reason"] == "MAX_EXPOSURE_EXCEEDED"


# ---------------------------------------------------------------------------
# 3. /api/system-events
# ---------------------------------------------------------------------------

class TestSystemEvents:
    def test_system_events_returns_200(self, client):
        assert client.get("/api/system-events").status_code == 200

    def test_system_events_structure(self, client):
        data = json.loads(client.get("/api/system-events").data)
        assert data["status"] == "SUCCESS"
        assert "events" in data
        assert isinstance(data["events"], list)
        assert "count" in data
        assert data["count"] >= 1

    def test_system_events_engine_heartbeat_present(self, client):
        data = json.loads(client.get("/api/system-events").data)
        heartbeats = [e for e in data["events"] if e.get("event_type") == "ENGINE_HEARTBEAT"]
        assert len(heartbeats) >= 1, "ENGINE_HEARTBEAT must always be present"

    def test_system_events_limit_param(self, client):
        data = json.loads(client.get("/api/system-events?limit=1").data)
        assert len(data["events"]) <= 1

    def test_system_events_heartbeat_fields(self, client):
        data = json.loads(client.get("/api/system-events").data)
        hb = next(e for e in data["events"] if e.get("event_type") == "ENGINE_HEARTBEAT")
        assert "timestamp" in hb
        assert "message" in hb
        assert "status" in hb
        assert "source" in hb


# ---------------------------------------------------------------------------
# 4. /api/trade-history enriched with 40-field canonical data
# ---------------------------------------------------------------------------

class TestTradeHistory:
    def test_trade_history_returns_200(self, client):
        assert client.get("/api/trade-history").status_code == 200

    def test_trade_history_structure(self, client):
        data = json.loads(client.get("/api/trade-history").data)
        assert data["status"] == "SUCCESS"
        assert "trades" in data
        assert isinstance(data["trades"], list)
        assert "total_trades" in data
        assert "timestamp" in data

    def test_trade_history_15_lifecycle_fields(self, client):
        """Every closed trade must carry all 15 lifecycle question fields."""
        data = json.loads(client.get("/api/trade-history").data)
        lifecycle_fields = [
            "signal_time", "order_submit_time", "fill_time", "close_time",
            "strategy", "timeframe",
            "entry_price", "exit_price",
            "stop_loss", "take_profit",
            "balance_before_entry", "equity_before_entry",
            "balance_after_exit", "equity_after_exit",
            "net_pnl", "fees", "close_reason",
        ]
        for trade in data.get("trades", []):
            for field in lifecycle_fields:
                assert field in trade, f"Trade missing lifecycle field: {field!r}"

    def test_trade_history_source_field(self, client):
        data = json.loads(client.get("/api/trade-history").data)
        assert "source" in data
        assert data["source"] in ("canonical_telemetry", "ledger_fallback")


# ---------------------------------------------------------------------------
# 5. /api/trade-events canonical 40-field store
# ---------------------------------------------------------------------------

class TestTradeEvents:
    def test_trade_events_returns_200(self, client):
        assert client.get("/api/trade-events").status_code == 200

    def test_trade_events_structure(self, client):
        data = json.loads(client.get("/api/trade-events").data)
        assert data["status"] == "SUCCESS"
        assert "events" in data
        assert isinstance(data["events"], list)

    def test_trade_events_canonical_fields_present(self, client):
        data = json.loads(client.get("/api/trade-events").data)
        canonical_fields = [
            "trade_id", "symbol", "strategy", "timeframe", "side", "status",
            "signal_timestamp", "order_submit_timestamp", "fill_timestamp",
            "close_timestamp", "entry_order_id", "exit_order_id",
            "entry_price", "exit_price", "quantity", "notional",
            "stop_loss", "take_profit", "entry_fee", "exit_fee", "total_fees",
            "gross_pnl", "net_pnl",
            "equity_before_entry", "equity_after_entry",
            "equity_before_exit", "equity_after_exit",
            "cash_before_entry", "cash_after_entry",
            "cash_before_exit", "cash_after_exit",
            "close_reason", "source", "provenance"
        ]
        for ev in data.get("events", []):
            missing = [f for f in canonical_fields if f not in ev]
            assert not missing, f"Trade event missing fields: {missing}"

    def test_trade_events_filter_by_symbol(self, client):
        resp = client.get("/api/trade-events?symbol=BTCUSDT")
        assert resp.status_code == 200
        data = json.loads(resp.data)
        for ev in data["events"]:
            assert ev["symbol"] == "BTCUSDT"

    def test_trade_events_filter_by_status(self, client):
        for status in ["OPEN", "CLOSED"]:
            resp = client.get(f"/api/trade-events?status={status}")
            assert resp.status_code == 200
            data = json.loads(resp.data)
            for ev in data["events"]:
                assert ev["status"] == status


# ---------------------------------------------------------------------------
# 6. /api/balance-events
# ---------------------------------------------------------------------------

class TestBalanceEvents:
    def test_balance_events_returns_200(self, client):
        assert client.get("/api/balance-events").status_code == 200

    def test_balance_events_structure(self, client):
        data = json.loads(client.get("/api/balance-events").data)
        assert data["status"] == "SUCCESS"
        assert "events" in data
        assert "count" in data

    def test_balance_events_limit_param(self, client):
        data = json.loads(client.get("/api/balance-events?limit=10").data)
        assert len(data["events"]) <= 10


# ---------------------------------------------------------------------------
# 7. Equity accounting invariant
# ---------------------------------------------------------------------------

class TestEquityAccountingInvariant:
    def test_account_equity_no_double_count(self, client):
        """total_equity must equal cash + crypto_holdings_value. No double counting."""
        resp = client.get("/api/account")
        assert resp.status_code == 200
        account = json.loads(resp.data).get("account", {})
        cash = float(account.get("usdt_total_cash", 0.0))
        crypto = float(account.get("crypto_holdings_value", 0.0))
        total = float(account.get("total_equity", 0.0))
        assert abs(total - (cash + crypto)) < 0.01, (
            f"Accounting violated: total_equity={total} != cash({cash}) + crypto({crypto})"
        )

    def test_status_equity_no_double_count(self, client):
        resp = client.get("/api/status")
        assert resp.status_code == 200
        data = json.loads(resp.data)
        cash = float(data.get("cash", 0.0))
        crypto = float(data.get("crypto_holdings_value", 0.0))
        equity = float(data.get("equity", 0.0))
        if cash > 0 or crypto > 0:
            assert abs(equity - (cash + crypto)) < 0.01, (
                f"Status equity violated: equity={equity} != cash({cash}) + crypto({crypto})"
            )


# ---------------------------------------------------------------------------
# 8. Full lifecycle round-trip via TelemetryManager
# ---------------------------------------------------------------------------

class TestFullLifecycleRoundTrip:
    def test_complete_lifecycle_all_fields(self, isolated_telemetry):
        """Full open-close lifecycle must produce a canonical event answering all 15 questions."""
        tm = isolated_telemetry
        # OPEN
        tm.record_trade_event({
            "trade_id": "LIFECYCLE-001",
            "symbol": "BTCUSDT",
            "strategy": "ADX_EMA",
            "timeframe": "15m",
            "side": "BUY",
            "status": "OPEN",
            "entry_order_id": "ORD-ENTRY-001",
            "signal_timestamp": "2026-08-18T10:00:00Z",
            "order_submit_timestamp": "2026-08-18T10:00:00Z",
            "fill_timestamp": "2026-08-18T10:00:01Z",
            "entry_price": 60000.0,
            "quantity": 0.01,
            "notional": 600.0,
            "stop_loss": 59400.0,
            "take_profit": 61200.0,
            "entry_fee": 0.6,
            "equity_before_entry": 10000.0,
            "equity_after_entry": 10000.0,
            "cash_before_entry": 10000.0,
            "cash_after_entry": 9400.0,
            "profitability_decision": "ACCEPTED",
            "profitability_reason": "NET_EDGE_POSITIVE",
            "risk_decision": "ACCEPTED",
            "risk_reason": "WITHIN_EXPOSURE_LIMITS",
        })
        # CLOSE
        tm.record_trade_event({
            "trade_id": "LIFECYCLE-001",
            "status": "CLOSED",
            "exit_order_id": "ORD-EXIT-001",
            "close_timestamp": "2026-08-18T10:05:01Z",
            "exit_price": 61200.0,
            "gross_pnl": 12.0,
            "exit_fee": 0.61,
            "total_fees": 1.21,
            "net_pnl": 10.79,
            "equity_before_exit": 10000.0,
            "equity_after_exit": 10010.79,
            "cash_before_exit": 9400.0,
            "cash_after_exit": 10010.79,
            "close_reason": "TAKE_PROFIT_HIT",
        })
        closed = tm.query_trades(status="CLOSED")
        assert len(closed) == 1
        t = closed[0]
        # Verify all 15 lifecycle questions are answered
        assert t["signal_timestamp"] == "2026-08-18T10:00:00Z"
        assert t["fill_timestamp"] == "2026-08-18T10:00:01Z"
        assert t["close_timestamp"] == "2026-08-18T10:05:01Z"
        assert t["strategy"] == "ADX_EMA"
        assert t["timeframe"] == "15m"
        assert t["profitability_decision"] == "ACCEPTED"
        assert t["risk_decision"] == "ACCEPTED"
        assert t["entry_price"] == 60000.0
        assert t["exit_price"] == 61200.0
        assert t["stop_loss"] == 59400.0
        assert t["take_profit"] == 61200.0
        assert t["cash_before_entry"] == 10000.0
        assert t["cash_after_entry"] == 9400.0
        assert t["equity_before_entry"] == 10000.0
        assert t["cash_before_exit"] == 9400.0
        assert t["cash_after_exit"] == 10010.79
        assert t["equity_after_exit"] == 10010.79
        assert t["net_pnl"] == 10.79
        assert t["total_fees"] == 1.21
        assert t["close_reason"] == "TAKE_PROFIT_HIT"

    def test_lifecycle_duration_auto_calculated(self, isolated_telemetry):
        tm = isolated_telemetry
        tm.record_trade_event({
            "trade_id": "DUR-001",
            "symbol": "ETHUSDT",
            "strategy": "ADX_EMA",
            "side": "BUY",
            "status": "CLOSED",
            "fill_timestamp": "2026-08-18T10:00:00Z",
            "close_timestamp": "2026-08-18T10:05:00Z",
            "entry_price": 3000.0,
            "exit_price": 3050.0,
            "net_pnl": 50.0,
            "close_reason": "TAKE_PROFIT_HIT",
        })
        closed = tm.query_trades(status="CLOSED")
        assert len(closed) == 1
        assert closed[0]["duration_seconds"] == pytest.approx(300.0, abs=1.0)

    def test_balance_event_no_double_count(self, isolated_telemetry):
        tm = isolated_telemetry
        tm.record_balance_event({
            "event_type": "TRADE_CLOSE",
            "reason": "TAKE_PROFIT_HIT",
            "balance_before": 10000.0,
            "balance_after": 10010.79,
            "delta": 10.79,
            "realized_pnl_delta": 10.79,
            "fees_delta": 1.21,
            "trade_id": "LIFECYCLE-001",
            "symbol": "BTCUSDT",
        })
        events = tm.get_balance_events(limit=10)
        assert len(events) == 1
        ev = events[0]
        # balance_after - balance_before must equal delta exactly
        assert abs(ev["delta"] - (ev["balance_after"] - ev["balance_before"])) < 0.001
        # No double-counting: balance_after = balance_before + delta only
        assert abs(ev["balance_after"] - (ev["balance_before"] + ev["delta"])) < 0.001

    def test_signal_risk_decision_persisted(self, isolated_telemetry):
        tm = isolated_telemetry
        tm.record_signal_event({
            "signal_id": "sig-test-001",
            "symbol": "SOLUSDT",
            "strategy": "SCALPER",
            "timeframe": "1m",
            "decision": "BUY",
            "risk_decision": "REJECTED",
            "risk_reason": "DAILY_LOSS_EXCEEDED",
            "profitability_decision": "ACCEPTED",
            "profitability_reason": "NET_EDGE_POSITIVE",
        })
        sigs = tm.query_signals(symbol="SOLUSDT")
        assert len(sigs) == 1
        assert sigs[0]["risk_decision"] == "REJECTED"
        assert sigs[0]["risk_reason"] == "DAILY_LOSS_EXCEEDED"
        assert sigs[0]["profitability_decision"] == "ACCEPTED"
