import pytest

from exchanges.mt5_bridge_adapter import MT5BridgeAdapter, MT5BridgeError


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
