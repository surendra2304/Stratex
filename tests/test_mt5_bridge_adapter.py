import pytest

from exchanges.mt5_bridge_adapter import MT5BridgeAdapter, MT5BridgeError
from stratex_agents.order_intent import TradeIntent


class FakeResponse:
    def __init__(self, payload, status=200):
        self.payload = payload
        self.status = status

    def raise_for_status(self):
        if self.status >= 400:
            raise RuntimeError("http error")

    def json(self):
        return self.payload


class FakeSession:
    def __init__(self, payload):
        self.payload = payload
        self.calls = []

    def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        return FakeResponse(self.payload)


class SequencedFakeSession:
    def __init__(self, payloads):
        self.payloads = list(payloads)
        self.calls = []

    def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        if not self.payloads:
            raise AssertionError("Unexpected extra HTTP request from MT5 adapter")
        return FakeResponse(self.payloads.pop(0))


def test_adapter_disabled_by_default(monkeypatch):
    monkeypatch.delenv("MT5_BRIDGE_ENABLED", raising=False)
    adapter = MT5BridgeAdapter(base_url="http://127.0.0.1:8765", token="test-token")
    with pytest.raises(MT5BridgeError, match="disabled"):
        adapter.health()


def test_health_sends_bearer_token_and_requires_demo(monkeypatch):
    monkeypatch.setenv("MT5_BRIDGE_ENABLED", "true")
    session = FakeSession({"ok": True, "data": {"demo": True, "server": "ICMarketsSC-Demo"}})
    adapter = MT5BridgeAdapter(
        base_url="http://100.64.0.2:8765", token="test-token", session=session
    )
    assert adapter.health()["demo"] is True
    method, url, kwargs = session.calls[0]
    assert method == "GET"
    assert url.endswith("/v1/health")
    assert kwargs["headers"]["Authorization"] == "Bearer test-token"


def test_refuses_non_demo_account(monkeypatch):
    monkeypatch.setenv("MT5_BRIDGE_ENABLED", "true")
    session = FakeSession({"ok": True, "data": {"demo": False, "server": "ICMarketsSC-Live"}})
    adapter = MT5BridgeAdapter(
        base_url="http://100.64.0.2:8765", token="test-token", session=session
    )
    with pytest.raises(MT5BridgeError, match="non-demo"):
        adapter.health()


def test_mt5_position_mapping_preserves_each_hedged_position(monkeypatch):
    monkeypatch.setenv("MT5_BRIDGE_ENABLED", "true")
    session = FakeSession({"ok": True, "data": [
        {"ticket": 101, "symbol": "EURUSD", "type": 0, "volume": 0.1,
         "price_open": 1.08, "price_current": 1.09, "profit": 1.0, "leverage": 100},
        {"ticket": 202, "symbol": "EURUSD", "type": 1, "volume": 0.1,
         "price_open": 1.09, "price_current": 1.08, "profit": 1.0, "leverage": 100},
    ]})
    adapter = MT5BridgeAdapter(
        base_url="http://100.64.0.2:8765", token="test-token", session=session
    )
    positions = adapter.get_positions()
    assert len(positions) == 2
    assert {p.side for p in positions} == {"LONG", "SHORT"}
    assert all(p.exchange == "mt5" for p in positions)
    assert [p.position_id for p in positions] == ["101", "202"]


def test_order_validation_rejects_zero_volume(monkeypatch):
    monkeypatch.setenv("MT5_BRIDGE_ENABLED", "true")
    adapter = MT5BridgeAdapter(base_url="http://127.0.0.1:8765", token="token", session=FakeSession({}))
    with pytest.raises(ValueError, match="positive"):
        adapter.place_order("EURUSD", "BUY", "MARKET", 0)



def test_mt5_entry_adapter_sends_stop_loss_take_profit_and_client_id(monkeypatch):
    monkeypatch.setenv("MT5_BRIDGE_ENABLED", "true")
    session = FakeSession({"ok": True, "data": {
        "order": 501, "deal": 601, "status": "FILLED", "price": 1.1002,
        "volume_filled": 0.1, "commission": 0.0,
    }})
    adapter = MT5BridgeAdapter(
        base_url="http://100.64.0.2:8765", token="test-token", session=session
    )
    result = adapter.place_order(
        "EURUSD", "BUY", "MARKET", 0.1,
        stop_loss=1.09, take_profit=1.12, client_order_id="stx-mt5-entry-001",
    )
    method, url, kwargs = session.calls[0]
    assert method == "POST"
    assert url.endswith("/v1/orders")
    assert kwargs["json"]["stop_loss"] == 1.09
    assert kwargs["json"]["take_profit"] == 1.12
    assert kwargs["json"]["client_order_id"] == "stx-mt5-entry-001"
    assert result.order_id == "501"


def test_mt5_adapter_accepts_normalized_demo_intent(monkeypatch):
    monkeypatch.setenv("MT5_BRIDGE_ENABLED", "true")
    session = SequencedFakeSession([
        {"ok": True, "data": {
            "approved": True, "recommended_volume": 0.2, "volume_min": 0.01,
            "volume_step": 0.01, "account_currency": "USD",
        }},
        {"ok": True, "data": {
            "order": 777, "deal": 888, "status": "FILLED", "price": 1.1002,
            "volume_filled": 0.1, "commission": 0.0,
        }},
    ])
    adapter = MT5BridgeAdapter(
        base_url="http://100.64.0.2:8765", token="test-token", session=session
    )
    intent = TradeIntent(
        venue="mt5", symbol="EURUSD", side="BUY", order_type="MARKET", quantity=0.1,
        environment="mt5_demo", stop_loss=1.09, take_profit=1.12,
        strategy="demo-test", client_order_id="stx-mt5-oms-001",
    )
    result = adapter.submit_intent(intent)
    assert result["status"] == "FILLED"
    assert result["client_order_id"] == "stx-mt5-oms-001"
    assert len(session.calls) == 2
    assert session.calls[0][1].endswith("/v1/risk/size")
    assert session.calls[1][1].endswith("/v1/orders")


def test_mt5_adapter_requires_bracket_protection_for_entries(monkeypatch):
    monkeypatch.setenv("MT5_BRIDGE_ENABLED", "true")
    adapter = MT5BridgeAdapter(
        base_url="http://127.0.0.1:8765", token="test-token", session=FakeSession({})
    )
    with pytest.raises(ValueError, match="stop_loss and take_profit"):
        adapter.place_order("EURUSD", "BUY", "MARKET", 0.1)



def test_mt5_symbol_discovery_returns_contract_metadata(monkeypatch):
    monkeypatch.setenv("MT5_BRIDGE_ENABLED", "true")
    session = FakeSession({"ok": True, "data": {
        "count": 1, "returned": 1,
        "symbols": [{
            "symbol": "XAUUSD", "description": "Gold vs US Dollar", "path": "Metals\\Gold",
            "visible": True, "base_currency": "XAU", "profit_currency": "USD",
            "margin_currency": "USD", "digits": 2, "contract_size": 100,
            "volume_min": 0.01, "volume_max": 50, "volume_step": 0.01,
            "tick_size": 0.01, "tick_value": 1.0,
        }],
    }})
    adapter = MT5BridgeAdapter(
        base_url="http://100.64.0.2:8765", token="test-token", session=session
    )
    symbols = adapter.get_symbols(query="XAU", visible_only=False)
    assert symbols[0]["symbol"] == "XAUUSD"
    assert symbols[0]["contract_size"] == 100
    assert symbols[0]["volume_step"] == 0.01
    method, url, kwargs = session.calls[0]
    assert method == "GET"
    assert url.endswith("/v1/symbols")
    assert kwargs["params"]["query"] == "XAU"


def test_mt5_get_symbol_info_requires_exact_broker_symbol(monkeypatch):
    monkeypatch.setenv("MT5_BRIDGE_ENABLED", "true")
    session = FakeSession({"ok": True, "data": {
        "count": 2, "returned": 2,
        "symbols": [
            {"symbol": "XAUUSD.a", "contract_size": 100},
            {"symbol": "XAUUSD.pro", "contract_size": 100},
        ],
    }})
    adapter = MT5BridgeAdapter(
        base_url="http://100.64.0.2:8765", token="test-token", session=session
    )
    with pytest.raises(MT5BridgeError, match="not found"):
        adapter.get_symbol_info("XAUUSD")


def test_mt5_reconciliation_confirms_persistent_bridge_record(monkeypatch):
    monkeypatch.setenv("MT5_BRIDGE_ENABLED", "true")
    session = FakeSession({"ok": True, "data": {
        "client_order_id": "stx-mt5-reconcile-01",
        "status": "FILLED",
        "response": {"status": "FILLED", "order": 765, "deal": 876},
        "manual_reconciliation_required": False,
    }})
    adapter = MT5BridgeAdapter(base_url="http://100.64.0.2:8765", token="test-token", session=session)
    stored = {
        "client_order_id": "stx-mt5-reconcile-01",
        "payload": {"venue": "mt5", "environment": "mt5_demo", "client_order_id": "stx-mt5-reconcile-01"},
    }
    result = adapter.reconcile_intent(stored)
    assert result["confirmed"] is True
    assert result["status"] == "FILLED"
    assert result["result"]["order"] == 765


def test_mt5_reconciliation_keeps_ambiguous_bridge_record_unresolved(monkeypatch):
    monkeypatch.setenv("MT5_BRIDGE_ENABLED", "true")
    session = FakeSession({"ok": True, "data": {
        "client_order_id": "stx-mt5-reconcile-02",
        "status": "UNKNOWN",
        "response": None,
        "manual_reconciliation_required": True,
    }})
    adapter = MT5BridgeAdapter(base_url="http://100.64.0.2:8765", token="test-token", session=session)
    stored = {
        "client_order_id": "stx-mt5-reconcile-02",
        "payload": {"venue": "mt5", "environment": "mt5_demo", "client_order_id": "stx-mt5-reconcile-02"},
    }
    result = adapter.reconcile_intent(stored)
    assert result["confirmed"] is False
    assert result["status"] == "UNKNOWN"
    assert "manual reconciliation" in result["evidence"]

def test_mt5_adapter_order_check_method(monkeypatch):
    monkeypatch.setenv("MT5_BRIDGE_ENABLED", "true")
    session = FakeSession({"ok": True, "data": {
        "approved": True, "retcode": 0, "comment": "Done", "margin": 100.0,
        "symbol": "EURUSD", "side": "BUY", "volume": 0.1,
    }})
    adapter = MT5BridgeAdapter(
        base_url="http://100.64.0.2:8765", token="test-token", session=session
    )
    res = adapter.order_check("EURUSD", "BUY", "MARKET", 0.1, stop_loss=1.09, take_profit=1.12)
    assert res["approved"] is True
    assert res["margin"] == 100.0
    assert session.calls[0][1].endswith("/v1/orders/check")


def test_mt5_adapter_submit_intent_with_preflight_flag(monkeypatch):
    monkeypatch.setenv("MT5_BRIDGE_ENABLED", "true")
    monkeypatch.setenv("MT5_PREFLIGHT_CHECK", "true")
    session = SequencedFakeSession([
        # 1. /v1/risk/size
        {"ok": True, "data": {
            "approved": True, "recommended_volume": 0.1, "volume_min": 0.01,
            "volume_max": 100.0, "volume_step": 0.01, "risk_budget": 5.0,
            "risk_amount": 2.0, "margin_required": 100.0, "margin_budget": 200.0,
            "account_currency": "USD",
        }},
        # 2. /v1/orders/check
        {"ok": True, "data": {
            "approved": True, "retcode": 0, "comment": "Done", "margin": 100.0,
        }},
        # 3. /v1/orders
        {"ok": True, "data": {
            "order": 12345, "deal": 67890, "status": "FILLED",
            "price": 1.1001, "volume_filled": 0.1, "commission": 0.0,
        }},
    ])
    adapter = MT5BridgeAdapter(
        base_url="http://100.64.0.2:8765", token="test-token", session=session
    )
    intent = TradeIntent(
        venue="mt5", symbol="EURUSD", side="BUY", order_type="MARKET", quantity=0.1,
        environment="mt5_demo", stop_loss=1.09, take_profit=1.12,
        strategy="demo-test", client_order_id="stx-mt5-preflight-001",
    )
    result = adapter.submit_intent(intent)
    assert result["status"] == "FILLED"
    assert len(session.calls) == 3
    assert session.calls[0][1].endswith("/v1/risk/size")
    assert session.calls[1][1].endswith("/v1/orders/check")
    assert session.calls[2][1].endswith("/v1/orders")
