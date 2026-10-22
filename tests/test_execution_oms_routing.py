import types

import execution
import pytest
import stratex_agents.runtime as oms_runtime
from stratex_agents.oms import OMSResult


class FakeLegacyIdempotencyStore:
    def __init__(self):
        self.called = False

    def check_and_record(self, client_order_id, request_type):
        self.called = True
        return True, {"response": {"legacy_path": True, "client_order_id": client_order_id}}


class FakeRiskGate:
    def __init__(self, result=(True, "RISK_OK", "")):
        self.result = result
        self.calls = []

    def evaluate_risk(self, *args):
        self.calls.append(args)
        return self.result


class FakeOMSManager:
    def __init__(self, risk_authorizer, result=None):
        self.risk_authorizer = risk_authorizer
        self.result = result or OMSResult(
            client_order_id="stx-test-signal-001",
            venue="binance",
            status="FILLED",
            result={
                "status": "FILLED",
                "order_id": "12345",
                "client_order_id": "stx-test-signal-001",
                "quantity": 0.01,
                "avg_price": 100.0,
                "stop_loss": 95.0,
                "take_profit": 110.0,
                "stop_algo_id": 501,
                "take_profit_algo_id": 502,
                "venue": "binance",
                "environment": "binance_demo",
            },
        )
        self.submitted = []

    def submit(self, intent):
        self.submitted.append(intent)
        allowed, reason = self.risk_authorizer(intent)
        if not allowed:
            return OMSResult(
                client_order_id=intent.client_order_id,
                venue=intent.venue,
                status="REJECTED",
                reason=reason,
            )
        return self.result


def _allow_policy(monkeypatch):
    monkeypatch.setattr(execution, "_check_panic_and_kill_switch", lambda: None)
    monkeypatch.setattr(execution.ExecutionPolicy, "can_place_order", staticmethod(lambda: (True, "ALLOWED_FUTURES")))


def test_oms_feature_flag_routes_before_legacy_idempotency(monkeypatch):
    _allow_policy(monkeypatch)
    monkeypatch.setenv("STRATEX_ORDER_MANAGER_ENABLED", "true")
    sentinel_store = FakeLegacyIdempotencyStore()
    monkeypatch.setattr(execution, "get_idempotency_store", lambda: sentinel_store)

    observed = {}

    def fake_oms_dispatch(*args, **kwargs):
        observed["args"] = args
        observed["kwargs"] = kwargs
        return {"routed_via_oms": True}

    monkeypatch.setattr(execution, "_place_futures_market_order_via_oms", fake_oms_dispatch)
    risk_gate = FakeRiskGate()
    risk_context = {
        "current_equity": 1000.0,
        "active_positions": {},
        "entry_price": 100.0,
        "data_health_status": "OK",
    }
    result = execution.place_futures_market_order(
        "test-strategy", "BUY", "BTCUSDT", quantity="0.01", sl=95, tp=110,
        client_order_id="signal-001", leverage=5,
        risk_gate=risk_gate, risk_context=risk_context,
    )

    assert result == {"routed_via_oms": True}
    assert sentinel_store.called is False
    assert observed["kwargs"]["risk_gate"] is risk_gate
    assert observed["kwargs"]["risk_context"] == risk_context
    assert observed["args"][:3] == ("test-strategy", "BUY", "BTCUSDT")


def test_disabled_oms_flag_keeps_legacy_idempotency_path(monkeypatch):
    _allow_policy(monkeypatch)
    monkeypatch.setenv("STRATEX_ORDER_MANAGER_ENABLED", "false")
    store = FakeLegacyIdempotencyStore()
    monkeypatch.setattr(execution, "get_idempotency_store", lambda: store)
    monkeypatch.setattr(
        execution,
        "_place_futures_market_order_via_oms",
        lambda *args, **kwargs: pytest.fail("OMS dispatch must remain disabled by default"),
    )

    result = execution.place_futures_market_order(
        "test-strategy", "BUY", "BTCUSDT", quantity="0.01", sl=95, tp=110,
        client_order_id="signal-legacy-001", leverage=5,
    )

    assert store.called is True
    assert result == {"legacy_path": True, "client_order_id": "signal-legacy-001"}


def test_oms_helper_uses_existing_risk_gate_and_persists_protected_trade(monkeypatch):
    monkeypatch.setenv("BINANCE_DEMO_TRADING", "true")
    monkeypatch.setenv("LIVE_TRADING_ENABLED", "false")
    monkeypatch.setattr(execution.ExecutionPolicy, "can_place_order", staticmethod(lambda: (True, "ALLOWED_FUTURES")))
    risk_gate = FakeRiskGate()
    risk_context = {
        "current_equity": 1000.0,
        "active_positions": {},
        "entry_price": 100.0,
        "data_health_status": "OK",
    }
    captured = {}
    manager = FakeOMSManager(lambda intent: (True, "RISK_OK"))

    def fake_build(authorize):
        captured["authorize"] = authorize
        manager.risk_authorizer = authorize
        return manager

    monkeypatch.setattr(oms_runtime, "build_order_manager", fake_build)
    saved = []
    monkeypatch.setattr(execution, "_load_active_trades", lambda: [])
    monkeypatch.setattr(execution, "_save_active_trades", lambda trades: saved.extend(trades))
    monkeypatch.setattr(execution, "log_trade", lambda *args, **kwargs: None)

    result = execution._place_futures_market_order_via_oms(
        "test-strategy", "BUY", "BTCUSDT", 0.01, 95.0, 110.0,
        "signal-oms-001", 5, risk_gate=risk_gate, risk_context=risk_context,
    )

    assert risk_gate.calls
    assert result["_actual_price"] == 100.0
    assert result["_executed_qty"] == 0.01
    assert result["stop_algo_id"] == 501
    assert result["take_profit_algo_id"] == 502
    assert len(saved) == 1
    assert saved[0]["is_futures"] is True
    assert saved[0]["state"] == execution.OrderState.PROTECTED.value
    assert saved[0]["signal_id"] == "signal-oms-001"
    assert saved[0]["oms_client_order_id"] == "signal-oms-001"


def test_oms_helper_fails_closed_when_risk_gate_denies_trade(monkeypatch):
    monkeypatch.setenv("BINANCE_DEMO_TRADING", "true")
    monkeypatch.setenv("LIVE_TRADING_ENABLED", "false")
    monkeypatch.setattr(execution.ExecutionPolicy, "can_place_order", staticmethod(lambda: (True, "ALLOWED_FUTURES")))
    risk_gate = FakeRiskGate((False, "MAX_EXPOSURE_REACHED", "over cap"))
    captured = {}
    fake_manager = FakeOMSManager(None)

    def fake_build(authorize):
        captured["authorize"] = authorize
        fake_manager.risk_authorizer = authorize
        return fake_manager

    monkeypatch.setattr(oms_runtime, "build_order_manager", fake_build)
    monkeypatch.setattr(execution, "_load_active_trades", lambda: [])
    monkeypatch.setattr(execution, "_save_active_trades", lambda _trades: pytest.fail("rejected trade must not be persisted as open"))
    monkeypatch.setattr(execution, "log_trade", lambda *args, **kwargs: None)

    result = execution._place_futures_market_order_via_oms(
        "test-strategy", "BUY", "BTCUSDT", 0.01, 95.0, 110.0,
        "signal-oms-denied", 5,
        risk_gate=risk_gate,
        risk_context={
            "current_equity": 1000.0,
            "active_positions": {},
            "entry_price": 100.0,
            "data_health_status": "OK",
        },
    )
    assert result is None
    assert len(risk_gate.calls) == 1
    assert fake_manager.submitted[0].symbol == "BTCUSDT"
