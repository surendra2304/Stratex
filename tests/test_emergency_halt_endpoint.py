"""Tests for Safety Invariant 3: Atomic Emergency Halt (/api/v1/emergency-halt)."""
from __future__ import annotations

import os
import pytest

import dashboard
from panic_state import is_kill_switch_locked, is_panic_active


@pytest.fixture
def emergency_client(tmp_path, monkeypatch):
    monkeypatch.setenv("PANIC_STATE_FILE", str(tmp_path / "panic_state.json"))
    monkeypatch.setenv("KILL_SWITCH_LOCK_FILE", str(tmp_path / "KILL_SWITCH_ACTIVE.lock"))
    dashboard.app.config["TESTING"] = True
    with dashboard.app.test_client() as c:
        yield c


def test_emergency_halt_requires_auth(emergency_client):
    res = emergency_client.post("/api/v1/emergency-halt")
    assert res.status_code in {401, 503}


def test_emergency_halt_locks_oms_and_cancels_orders(emergency_client, control_auth, monkeypatch):
    memora_calls = []

    class FakeMemora:
        def record_interaction(self, **kwargs):
            memora_calls.append(kwargs)
            return {"status": "ok"}

    import autonomy.memora_audit as audit_mod
    monkeypatch.setattr(audit_mod, "memora_client", FakeMemora())

    res = emergency_client.post("/api/v1/emergency-halt", headers=control_auth)
    assert res.status_code == 200
    data = res.get_json()
    assert data["status"] == "ok"
    assert data["oms_state"] == "LOCKED"
    assert "steps" in data
    assert is_panic_active()
    assert is_kill_switch_locked()
    assert len(memora_calls) == 1
    assert memora_calls[0]["event_type"] == "emergency_halt"
    assert memora_calls[0]["metadata"]["oms_state"] == "LOCKED"


def test_emergency_halt_blocks_subsequent_oms_orders(emergency_client, control_auth, tmp_path, monkeypatch):
    monkeypatch.setenv("STRATEX_EXECUTION_MODE", "DEMO")
    monkeypatch.setenv("TRADING_MODE", "FUTURES")
    monkeypatch.setenv("TESTNET_ENABLED", "true")
    monkeypatch.setenv("STRATEX_ALLOWED_EXECUTION_VENUES", "binance")

    from stratex_agents.oms import StratexOrderManager
    from stratex_agents.order_intent import TradeIntent
    from stratex_agents.order_store import OrderIntentStore

    # First trigger emergency halt
    res = emergency_client.post("/api/v1/emergency-halt", headers=control_auth)
    assert res.status_code == 200

    # Now verify OMS blocks orders
    store = OrderIntentStore(str(tmp_path / "orders.sqlite3"))
    oms = StratexOrderManager(
        venues={"binance": None},
        authorize_intent=lambda intent: (True, "OK"),
        store=store,
    )
    intent = TradeIntent(
        venue="binance",
        symbol="BTCUSDT",
        side="BUY",
        order_type="MARKET",
        quantity=0.01,
        environment="binance_testnet",
        stop_loss=98.0,
        take_profit=105.0,
        strategy="test-strat",
        client_order_id="stx-halted-001",
    )
    result = oms.submit(intent)
    assert result.status == "REJECTED"
    assert "kill switch is active" in result.reason.lower() or "panic switch is active" in result.reason.lower()
