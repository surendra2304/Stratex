import sqlite3

import pytest

from stratex_agents.order_intent import TradeIntent
from stratex_agents.order_store import OrderIntentStore


def make_intent(client_id="stx-audit-001"):
    return TradeIntent(
        venue="binance", symbol="BTCUSDT", side="BUY", order_type="MARKET",
        quantity=0.01, environment="binance_demo", stop_loss=95, take_profit=110,
        strategy="audit-test", client_order_id=client_id,
        metadata={"private_note": "must not be copied into audit"},
    )


def test_order_store_appends_reservation_and_transition_events(tmp_path):
    store = OrderIntentStore(str(tmp_path / "orders.sqlite3"))
    intent = make_intent()
    store.reserve(intent.client_order_id, intent.payload_hash(), intent.to_dict())
    store.transition(intent.client_order_id, "SUBMITTING")
    store.transition(
        intent.client_order_id,
        "FILLED",
        result={
            "status": "FILLED", "order_id": "demo-123", "avg_price": 100.0,
            "api_key": "never-audit-this",
        },
    )
    events = store.list_audit_events(client_order_id=intent.client_order_id)
    assert [event["event_type"] for event in events] == [
        "INTENT_RESERVED", "ORDER_STATE_TRANSITION", "ORDER_STATE_TRANSITION",
    ]
    assert events[0]["previous_status"] is None
    assert events[0]["new_status"] == "RESERVED"
    assert events[0]["details"]["symbol"] == "BTCUSDT"
    assert "metadata" not in events[0]["details"]
    assert events[-1]["previous_status"] == "SUBMITTING"
    assert events[-1]["new_status"] == "FILLED"
    assert events[-1]["details"]["result"] == {
        "status": "FILLED", "order_id": "demo-123", "avg_price": 100.0,
    }
    assert "api_key" not in str(events)


def test_order_store_audit_events_are_append_only_at_database_level(tmp_path):
    path = str(tmp_path / "orders.sqlite3")
    store = OrderIntentStore(path)
    intent = make_intent()
    store.reserve(intent.client_order_id, intent.payload_hash(), intent.to_dict())
    event_id = store.list_audit_events()[0]["event_id"]
    with sqlite3.connect(path) as conn:
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            conn.execute(
                "UPDATE order_audit_events SET event_type='TAMPERED' WHERE event_id=?",
                (event_id,),
            )
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            conn.execute("DELETE FROM order_audit_events WHERE event_id=?", (event_id,))


def test_order_store_rejects_lifecycle_regression_and_keeps_audit_intact(tmp_path):
    store = OrderIntentStore(str(tmp_path / "orders.sqlite3"))
    intent = make_intent()
    store.reserve(intent.client_order_id, intent.payload_hash(), intent.to_dict())
    store.transition(intent.client_order_id, "SUBMITTING")
    store.transition(intent.client_order_id, "PARTIALLY_FILLED")
    event_count = len(store.list_audit_events(client_order_id=intent.client_order_id))
    with pytest.raises(ValueError, match="Invalid order lifecycle transition"):
        store.transition(intent.client_order_id, "ACCEPTED")
    assert store.get(intent.client_order_id)["status"] == "PARTIALLY_FILLED"
    assert len(store.list_audit_events(client_order_id=intent.client_order_id)) == event_count


def test_order_store_records_duplicate_request_without_changing_state(tmp_path):
    store = OrderIntentStore(str(tmp_path / "orders.sqlite3"))
    intent = make_intent()
    store.reserve(intent.client_order_id, intent.payload_hash(), intent.to_dict())
    store.transition(intent.client_order_id, "SUBMITTING")
    store.transition(intent.client_order_id, "FILLED", result={"status": "FILLED"})
    duplicate = store.reserve(intent.client_order_id, intent.payload_hash(), intent.to_dict())
    assert duplicate["kind"] == "duplicate"
    assert store.get(intent.client_order_id)["status"] == "FILLED"
    assert store.list_audit_events()[-1]["event_type"] == "DUPLICATE_REQUEST"
