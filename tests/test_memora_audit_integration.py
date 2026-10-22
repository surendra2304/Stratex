"""Tests for Memora audit trail integration for trade intents, risk rejections and emergency halt."""
from __future__ import annotations

from typing import Any

from autonomy.memora_audit import (
    publish_emergency_halt_audit,
    publish_risk_rejection_audit,
    publish_trade_intent_audit,
)
from stratex_agents.oms import StratexOrderManager
from stratex_agents.order_intent import TradeIntent
from stratex_agents.order_store import OrderIntentStore


class FakeMemoraClient:
    def __init__(self, should_fail: bool = False):
        self.should_fail = should_fail
        self.calls: list[dict[str, Any]] = []

    def record_interaction(self, agent_name: str, user_input: str, agent_output: str,
                           event_type: str = "dialogue", tags=None, metadata=None):
        if self.should_fail:
            raise ConnectionError("Memora cluster unreachable")
        call_info = {
            "agent_name": agent_name,
            "user_input": user_input,
            "agent_output": agent_output,
            "event_type": event_type,
            "tags": tags or [],
            "metadata": metadata or {},
        }
        self.calls.append(call_info)
        return {"status": "ok", "recorded": True}


def make_test_intent():
    return TradeIntent(
        venue="binance",
        symbol="BTCUSDT",
        side="BUY",
        order_type="MARKET",
        quantity=0.01,
        environment="binance_demo",
        stop_loss=98.0,
        take_profit=105.0,
        strategy="memora-test-strat",
        client_order_id="stx-memora-001",
    )


def test_publish_trade_intent_audit_success():
    client = FakeMemoraClient()
    intent = make_test_intent()
    res = publish_trade_intent_audit(
        intent,
        "FILLED",
        result={"status": "FILLED", "avg_price": 100.0, "order_id": 12345},
        client=client,
    )
    assert res is True
    assert len(client.calls) == 1
    call = client.calls[0]
    assert call["agent_name"] == "stratex"
    assert call["event_type"] == "trade_intent_completed"
    assert "BTCUSDT" in call["user_input"]
    assert "filled" in call["tags"]
    assert call["metadata"]["client_order_id"] == "stx-memora-001"
    assert call["metadata"]["status"] == "FILLED"


def test_publish_risk_rejection_audit_success():
    client = FakeMemoraClient()
    intent = make_test_intent()
    res = publish_risk_rejection_audit(
        intent,
        "MAX_MARGIN_EXCEEDED",
        details="Required margin exceeds hard limit of $1,000",
        client=client,
    )
    assert res is True
    assert len(client.calls) == 1
    call = client.calls[0]
    assert call["agent_name"] == "stratex"
    assert call["event_type"] == "risk_rejection"
    assert "risk_rejection" in call["tags"]
    assert call["metadata"]["risk_rejection_reason"] == "MAX_MARGIN_EXCEEDED"


def test_publish_emergency_halt_audit_success():
    client = FakeMemoraClient()
    res = publish_emergency_halt_audit(
        actor="operator_api",
        reason="Market turbulence safety abort",
        orders_cancelled=5,
        client=client,
    )
    assert res is True
    assert len(client.calls) == 1
    call = client.calls[0]
    assert call["agent_name"] == "stratex"
    assert call["event_type"] == "emergency_halt"
    assert call["metadata"]["orders_cancelled"] == 5
    assert call["metadata"]["oms_state"] == "LOCKED"


def test_memora_audit_failsafe_on_exception():
    client = FakeMemoraClient(should_fail=True)
    intent = make_test_intent()
    # Must fail safely and return False without raising
    assert publish_trade_intent_audit(intent, "FILLED", client=client) is False
    assert publish_risk_rejection_audit(intent, "REASON", client=client) is False
    assert publish_emergency_halt_audit("actor", "reason", client=client) is False


def test_oms_submit_publishes_memora_audits(monkeypatch, tmp_path):
    import autonomy.memora_audit as audit_mod

    fake_client = FakeMemoraClient()
    monkeypatch.setattr(audit_mod, "memora_client", fake_client)
    monkeypatch.setenv("STRATEX_EXECUTION_MODE", "DEMO")
    monkeypatch.setenv("TRADING_MODE", "FUTURES")
    monkeypatch.setenv("BINANCE_DEMO_TRADING", "true")
    monkeypatch.setenv("TESTNET_ENABLED", "true")
    monkeypatch.setenv("STRATEX_ALLOWED_EXECUTION_VENUES", "binance")

    store = OrderIntentStore(str(tmp_path / "orders.sqlite3"))

    class FakeVenue:
        def submit_intent(self, intent):
            return {"status": "FILLED", "order_id": 999, "avg_price": 100.0}

    # 1. Test risk rejection audit
    oms_reject = StratexOrderManager(
        venues={"binance": FakeVenue()},
        authorize_intent=lambda intent: (False, "MANDATORY_STOP_LOSS_BREACH"),
        store=store,
        kill_switch_check=lambda: (False, ""),
    )
    res_reject = oms_reject.submit(make_test_intent())
    assert res_reject.status == "REJECTED"
    assert len(fake_client.calls) == 1
    assert fake_client.calls[0]["event_type"] == "risk_rejection"
    assert fake_client.calls[0]["metadata"]["risk_rejection_reason"] == "MANDATORY_STOP_LOSS_BREACH"

    # 2. Test successful fill audit
    intent2 = TradeIntent(
        venue="binance",
        symbol="BTCUSDT",
        side="BUY",
        order_type="MARKET",
        quantity=0.01,
        environment="binance_demo",
        stop_loss=98.0,
        take_profit=105.0,
        strategy="memora-test-strat",
        client_order_id="stx-memora-002",
    )
    oms_success = StratexOrderManager(
        venues={"binance": FakeVenue()},
        authorize_intent=lambda intent: (True, "OK"),
        store=store,
        kill_switch_check=lambda: (False, ""),
    )
    res_success = oms_success.submit(intent2)
    assert res_success.status == "FILLED"
    assert len(fake_client.calls) == 2
    assert fake_client.calls[1]["event_type"] == "trade_intent_completed"
    assert fake_client.calls[1]["metadata"]["client_order_id"] == "stx-memora-002"
    assert fake_client.calls[1]["metadata"]["status"] == "FILLED"
