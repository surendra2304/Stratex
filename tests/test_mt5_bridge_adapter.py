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
    session = FakeSession({"ok": True, "data": {
        "order": 777, "deal": 888, "status": "FILLED", "price": 1.1002,
        "volume_filled": 0.1, "commission": 0.0,
    }})
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


def test_mt5_adapter_requires_bracket_protection_for_entries(monkeypatch):
    monkeypatch.setenv("MT5_BRIDGE_ENABLED", "true")
    adapter = MT5BridgeAdapter(
        base_url="http://127.0.0.1:8765", token="test-token", session=FakeSession({})
    )
    with pytest.raises(ValueError, match="stop_loss and take_profit"):
        adapter.place_order("EURUSD", "BUY", "MARKET", 0.1)
