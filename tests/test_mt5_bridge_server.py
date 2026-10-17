import types

import pytest

import mt5_bridge.server as bridge


class FakeMT5:
    TRADE_ACTION_DEAL = 1
    TRADE_ACTION_PENDING = 5
    ORDER_TYPE_BUY = 0
    ORDER_TYPE_SELL = 1
    ORDER_TYPE_BUY_LIMIT = 2
    ORDER_TYPE_SELL_LIMIT = 3
    ORDER_TYPE_BUY_STOP = 4
    ORDER_TYPE_SELL_STOP = 5
    ORDER_TYPE_BUY_STOP_LIMIT = 6
    ORDER_TYPE_SELL_STOP_LIMIT = 7
    ORDER_TIME_GTC = 0
    TRADE_RETCODE_DONE = 10009
    TRADE_RETCODE_PLACED = 10008
    TRADE_RETCODE_DONE_PARTIAL = 10010

    def __init__(self, send_error=None):
        self.calls = []
        self.send_error = send_error
        self.info = types.SimpleNamespace(
            visible=True, volume_step=0.01, volume_min=0.01, volume_max=100.0,
            digits=5, point=0.00001, trade_stops_level=0, filling_mode=1,
        )
        self.tick = types.SimpleNamespace(bid=1.1000, ask=1.1002, last=1.1001, volume=10)

    def symbol_info(self, symbol):
        return self.info if symbol == "EURUSD" else None

    def symbol_info_tick(self, symbol):
        return self.tick if symbol == "EURUSD" else None

    def order_send(self, order):
        self.calls.append(order)
        if self.send_error:
            raise self.send_error
        return types.SimpleNamespace(
            retcode=self.TRADE_RETCODE_DONE, order=123, deal=456,
            price=order["price"], volume=order["volume"], comment="filled",
        )

    @staticmethod
    def last_error():
        return (0, "no error")


@pytest.fixture
def bridge_client(monkeypatch, tmp_path):
    mt5 = FakeMT5()
    monkeypatch.setattr(bridge, "_mt5", lambda: (mt5, types.SimpleNamespace(server="ICMarketsSC-Demo")))
    monkeypatch.setattr(bridge, "TOKEN", "test-bridge-token")
    monkeypatch.setenv("MT5_BRIDGE_IDEMPOTENCY_DB", str(tmp_path / "orders.sqlite3"))
    bridge._ORDER_STORES.clear()
    bridge.app.config.update(TESTING=True)
    return bridge.app.test_client(), mt5


def _body(client_order_id="stx-order-001", **overrides):
    body = {
        "symbol": "EURUSD", "side": "BUY", "type": "MARKET", "volume": 0.1,
        "stop_loss": 1.09, "take_profit": 1.12, "client_order_id": client_order_id,
    }
    body.update(overrides)
    return body


def test_market_entry_sends_sl_tp_and_is_idempotent(bridge_client):
    client, mt5 = bridge_client
    headers = {"Authorization": "Bearer test-bridge-token"}
    body = _body()
    first = client.post("/v1/orders", json=body, headers=headers)
    assert first.status_code == 200
    assert first.json["data"]["status"] == "FILLED"
    sent = mt5.calls[0]
    assert sent["sl"] == 1.09
    assert sent["tp"] == 1.12
    assert sent["comment"] == "STX:stx-order-001"

    duplicate = client.post("/v1/orders", json=body, headers=headers)
    assert duplicate.status_code == 200
    assert duplicate.json["data"]["duplicate"] is True
    assert len(mt5.calls) == 1


def test_reused_client_id_with_different_payload_is_rejected(bridge_client):
    client, mt5 = bridge_client
    headers = {"Authorization": "Bearer test-bridge-token"}
    assert client.post("/v1/orders", json=_body(), headers=headers).status_code == 200
    response = client.post("/v1/orders", json=_body(take_profit=1.13), headers=headers)
    assert response.status_code == 409
    assert len(mt5.calls) == 1


def test_ambiguous_mt5_timeout_is_never_resubmitted(bridge_client, monkeypatch, tmp_path):
    client, mt5 = bridge_client
    mt5.send_error = TimeoutError("ambiguous transport error")
    headers = {"Authorization": "Bearer test-bridge-token"}
    body = _body("stx-timeout-001")
    first = client.post("/v1/orders", json=body, headers=headers)
    assert first.status_code == 503
    assert first.json["error"] == "ORDER_STATE_UNKNOWN"
    again = client.post("/v1/orders", json=body, headers=headers)
    assert again.status_code == 409
    assert len(mt5.calls) == 1


def test_entry_without_protection_is_rejected_before_order_send(bridge_client):
    client, mt5 = bridge_client
    response = client.post(
        "/v1/orders", json=_body(stop_loss=None, take_profit=None),
        headers={"Authorization": "Bearer test-bridge-token"},
    )
    assert response.status_code == 400
    assert len(mt5.calls) == 0


def test_client_id_route_surfaces_unresolved_order(bridge_client):
    client, mt5 = bridge_client
    mt5.send_error = TimeoutError("ambiguous transport error")
    headers = {"Authorization": "Bearer test-bridge-token"}
    client.post("/v1/orders", json=_body("stx-status-001"), headers=headers)
    response = client.get("/v1/orders/by-client-id/stx-status-001", headers=headers)
    assert response.status_code == 200
    assert response.json["data"]["status"] == "UNKNOWN"
    assert response.json["data"]["manual_reconciliation_required"] is True