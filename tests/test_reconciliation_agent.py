from stratex_agents.order_intent import TradeIntent
from stratex_agents.order_store import OrderIntentStore
from stratex_agents.reconciliation_agent import OrderReconciliationAgent


def make_intent(client_id="stx-reconcile-001"):
    return TradeIntent(
        venue="binance", symbol="BTCUSDT", side="BUY", order_type="MARKET",
        quantity=0.01, environment="binance_demo", stop_loss=95.0, take_profit=110.0,
        strategy="reconciliation-test", client_order_id=client_id,
    )


def reserved_store(tmp_path, trade=None, status="SUBMITTING"):
    store = OrderIntentStore(str(tmp_path / "reconcile.sqlite3"))
    intent = trade or make_intent()
    store.reserve(intent.client_order_id, intent.payload_hash(), intent.to_dict())
    if status != "RESERVED":
        store.transition(intent.client_order_id, status)
    return store, intent


def test_reconciliation_updates_unknown_only_on_confirmed_venue_evidence(tmp_path):
    store, intent = reserved_store(tmp_path, status="UNKNOWN")
    agent = OrderReconciliationAgent(
        store,
        {"binance": lambda _stored: {
            "confirmed": True, "status": "FILLED",
            "result": {"status": "FILLED", "order_id": "demo-123"},
            "evidence": "query by client order id",
        }},
    )
    result = agent.reconcile_once()
    record = store.get(intent.client_order_id)
    assert result["resolved"] == 1
    assert result["pending"] == 0
    assert record["status"] == "FILLED"
    assert record["result"]["order_id"] == "demo-123"
    assert result["execution_authority"] is False


def test_reconciliation_never_treats_missing_order_as_cancelled(tmp_path):
    store, intent = reserved_store(tmp_path, status="UNKNOWN")
    agent = OrderReconciliationAgent(
        store,
        {"binance": lambda _stored: {
            "confirmed": False, "status": "NOT_FOUND",
            "evidence": "query did not find order",
        }},
    )
    result = agent.reconcile_once()
    assert result["pending"] == 1
    assert store.get(intent.client_order_id)["status"] == "UNKNOWN"


def test_reconciliation_tracks_accepted_orders_for_future_polling(tmp_path):
    store, intent = reserved_store(tmp_path, status="SUBMITTING")
    agent = OrderReconciliationAgent(
        store,
        {"binance": lambda _stored: {
            "confirmed": True, "status": "NEW",
            "result": {"status": "NEW", "order_id": "demo-456"},
        }},
    )
    first = agent.reconcile_once()
    assert first["resolved"] == 1
    assert store.get(intent.client_order_id)["status"] == "ACCEPTED"

    polls = []
    def second_resolver(_stored):
        polls.append(True)
        return {
            "confirmed": True, "status": "FILLED",
            "result": {"status": "FILLED", "order_id": "demo-456"},
        }

    second = OrderReconciliationAgent(store, {"binance": second_resolver}).reconcile_once()
    assert len(polls) == 1
    assert second["resolved"] == 1
    assert store.get(intent.client_order_id)["status"] == "FILLED"


def test_reconciliation_does_not_fake_a_venue_when_resolver_missing(tmp_path):
    store, intent = reserved_store(tmp_path, status="RESERVED")
    result = OrderReconciliationAgent(store, {}).reconcile_once()
    assert result["pending"] == 1
    assert result["records"][0]["reason"] == "venue_resolver_unavailable"
    assert store.get(intent.client_order_id)["status"] == "RESERVED"


def test_reconciliation_isolates_resolver_exceptions(tmp_path):
    store, intent = reserved_store(tmp_path, status="SUBMITTING")
    def broken(_stored):
        raise TimeoutError("temporarily unavailable")
    result = OrderReconciliationAgent(store, {"binance": broken}).reconcile_once()
    assert result["errors"] == 1
    assert result["records"][0]["reason"] == "TimeoutError"
    assert store.get(intent.client_order_id)["status"] == "SUBMITTING"
