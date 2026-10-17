import pytest

from stratex_agents.order_intent import TradeIntent
from stratex_agents.venue.binance_futures_oms_adapter import BinanceFuturesOMSAdapter, BinanceVenueError


class FakeAgent:
    demo_trading = True

    def create_client(self, **kwargs):
        raise AssertionError("injected client should be used")


class FakeBinanceClient:
    stratex_environment = "demo_trading"

    def __init__(self, *, hedge_mode=True, fail_take_profit=False, fail_close=False):
        self.hedge_mode = hedge_mode
        self.fail_take_profit = fail_take_profit
        self.fail_close = fail_close
        self.order_calls = []
        self.algo_calls = []
        self.cancelled_algos = []
        self.configuration_calls = []
        self.position_amount = "0"
        self.reconcile_entry = {"status": "FILLED", "orderId": 111, "avgPrice": "100", "executedQty": "0.105"}
        self.reconcile_algo_status = "NEW"
        self.fail_reconcile_algo = False

    def futures_get_position_mode(self):
        return {"dualSidePosition": self.hedge_mode}

    def futures_exchange_info(self):
        return {"symbols": [{
            "symbol": "BTCUSDT", "status": "TRADING",
            "filters": [
                {"filterType": "LOT_SIZE", "stepSize": "0.001", "minQty": "0.001"},
                {"filterType": "MIN_NOTIONAL", "notional": "5"},
                {"filterType": "PRICE_FILTER", "tickSize": "0.1"},
            ],
        }]}

    def futures_symbol_ticker(self, symbol):
        return {"symbol": symbol, "price": "100.0"}

    def futures_account(self):
        return {"availableBalance": "10000"}

    def futures_position_information(self, symbol):
        return [{"symbol": symbol, "positionSide": "BOTH", "positionAmt": self.position_amount}]

    def futures_change_margin_type(self, symbol, marginType):
        self.configuration_calls.append(("margin", symbol, marginType))
        return {"symbol": symbol, "marginType": marginType}

    def futures_change_leverage(self, symbol, leverage):
        self.configuration_calls.append(("leverage", symbol, leverage))
        return {"symbol": symbol, "leverage": leverage}

    def futures_create_order(self, **params):
        self.order_calls.append(params)
        if params.get("newClientOrderId", "").startswith("stxcx-") and self.fail_close:
            raise TimeoutError("close ambiguous")
        if params.get("newClientOrderId", "").endswith("cx"):
            return {"status": "FILLED", "orderId": 222, "avgPrice": "100", "executedQty": params["quantity"]}
        return {"status": "FILLED", "orderId": 111, "avgPrice": "100", "executedQty": params["quantity"]}

    def futures_create_algo_order(self, **params):
        self.algo_calls.append(params)
        if params["type"] == "TAKE_PROFIT_MARKET" and self.fail_take_profit:
            raise TimeoutError("take-profit submission ambiguous")
        return {"algoId": len(self.algo_calls), "algoStatus": "NEW", "clientAlgoId": params["clientAlgoId"]}

    def futures_cancel_algo_order(self, **params):
        self.cancelled_algos.append(params)
        return {"algoId": params["algoId"], "algoStatus": "CANCELED"}

    def futures_get_order(self, **params):
        return dict(self.reconcile_entry)

    def futures_get_algo_order(self, *, symbol, clientAlgoId):
        if self.fail_reconcile_algo:
            raise TimeoutError("algo lookup unavailable")
        is_stop = clientAlgoId.endswith("-sl")
        return {
            "algoId": 501 if is_stop else 502,
            "algoStatus": self.reconcile_algo_status,
            "clientAlgoId": clientAlgoId,
            "positionSide": "LONG",
        }


def make_intent(*, environment="binance_demo", side="BUY", stop=95.0, take=110.0):
    return TradeIntent(
        venue="binance", symbol="BTCUSDT", side=side, order_type="MARKET",
        quantity=0.105, environment=environment, stop_loss=stop, take_profit=take,
        strategy="adapter-test", client_order_id="stx-binance-oms-001",
    )


def set_demo_env(monkeypatch):
    monkeypatch.setenv("LIVE_TRADING_ENABLED", "false")
    monkeypatch.setenv("BINANCE_DEMO_TRADING", "true")


def test_binance_market_intent_adds_protective_stop_and_take_profit(monkeypatch):
    set_demo_env(monkeypatch)
    client = FakeBinanceClient(hedge_mode=True)
    adapter = BinanceFuturesOMSAdapter(client=client, venue_agent=FakeAgent())
    result = adapter.submit_intent(make_intent())

    assert result["status"] == "FILLED"
    assert result["avg_price"] == 100
    assert result["stop_algo_id"] == 1
    assert result["take_profit_algo_id"] == 2
    assert client.order_calls[0]["positionSide"] == "LONG"
    assert ("margin", "BTCUSDT", "ISOLATED") in client.configuration_calls
    assert ("leverage", "BTCUSDT", 5) in client.configuration_calls
    assert len(client.algo_calls) == 2
    assert client.algo_calls[0]["type"] == "STOP_MARKET"
    assert client.algo_calls[0]["triggerPrice"] == "95.0"
    assert client.algo_calls[0]["positionSide"] == "LONG"
    assert client.algo_calls[0]["closePosition"] == "true"
    assert client.algo_calls[1]["type"] == "TAKE_PROFIT_MARKET"
    assert client.algo_calls[1]["triggerPrice"] == "110.0"


def test_binance_one_way_mode_omits_position_side_and_uses_close_position(monkeypatch):
    set_demo_env(monkeypatch)
    client = FakeBinanceClient(hedge_mode=False)
    result = BinanceFuturesOMSAdapter(client=client, venue_agent=FakeAgent()).submit_intent(make_intent())
    assert result["position_side"] == "BOTH"
    assert "positionSide" not in client.order_calls[0]
    assert all("positionSide" not in call for call in client.algo_calls)
    assert all(call["closePosition"] == "true" for call in client.algo_calls)


def test_invalid_filled_bracket_triggers_safety_close(monkeypatch):
    set_demo_env(monkeypatch)
    client = FakeBinanceClient(hedge_mode=False)
    result = BinanceFuturesOMSAdapter(client=client, venue_agent=FakeAgent()).submit_intent(
        make_intent(stop=105, take=110)
    )
    assert result["status"] == "REJECTED"
    assert "safety abort" in result["reason"]
    assert len(client.order_calls) == 2
    assert client.order_calls[1]["reduceOnly"] == "true"


def test_take_profit_failure_closes_position_then_cancels_acknowledged_stop(monkeypatch):
    set_demo_env(monkeypatch)
    client = FakeBinanceClient(hedge_mode=True, fail_take_profit=True)
    result = BinanceFuturesOMSAdapter(client=client, venue_agent=FakeAgent()).submit_intent(make_intent())

    assert result["status"] == "REJECTED"
    assert len(client.order_calls) == 2
    assert client.order_calls[1]["positionSide"] == "LONG"
    assert client.cancelled_algos == [{"symbol": "BTCUSDT", "algoId": 1}]


def test_unknown_safety_close_raises_so_oms_marks_order_unknown(monkeypatch):
    set_demo_env(monkeypatch)
    client = FakeBinanceClient(hedge_mode=True, fail_take_profit=True, fail_close=True)
    adapter = BinanceFuturesOMSAdapter(client=client, venue_agent=FakeAgent())
    with pytest.raises(BinanceVenueError, match="emergency close is unconfirmed"):
        adapter.submit_intent(make_intent())
    # If the protective take-profit couldn't be acknowledged and close is ambiguous,
    # keep the previously acknowledged stop in place as the remaining safety layer.
    assert client.cancelled_algos == []


def test_adapter_refuses_mismatched_intent_environment(monkeypatch):
    set_demo_env(monkeypatch)
    client = FakeBinanceClient()
    adapter = BinanceFuturesOMSAdapter(client=client, venue_agent=FakeAgent())
    with pytest.raises(BinanceVenueError, match="does not match Binance environment"):
        adapter.submit_intent(make_intent(environment="binance_testnet"))
    assert client.order_calls == []


def test_binance_adapter_refuses_entry_when_existing_position_needs_reconciliation(monkeypatch):
    set_demo_env(monkeypatch)
    client = FakeBinanceClient(hedge_mode=False)
    client.position_amount = "0.025"
    adapter = BinanceFuturesOMSAdapter(client=client, venue_agent=FakeAgent())
    with pytest.raises(BinanceVenueError, match="Existing BOTH position"):
        adapter.submit_intent(make_intent())
    assert client.order_calls == []
    assert client.algo_calls == []


def test_binance_reconciliation_requires_entry_and_both_protective_orders(monkeypatch):
    set_demo_env(monkeypatch)
    client = FakeBinanceClient()
    adapter = BinanceFuturesOMSAdapter(client=client, venue_agent=FakeAgent())
    stored = {
        "client_order_id": "stx-binance-oms-001",
        "status": "UNKNOWN",
        "payload": {
            "venue": "binance", "environment": "binance_demo", "symbol": "BTCUSDT",
            "side": "BUY", "order_type": "MARKET", "quantity": 0.105,
            "stop_loss": 95.0, "take_profit": 110.0,
            "client_order_id": "stx-binance-oms-001",
        },
    }
    result = adapter.reconcile_intent(stored)
    assert result["confirmed"] is True
    assert result["status"] == "FILLED"
    assert result["result"]["protection_verified"] is True
    assert result["result"]["stop_algo_id"] == 501
    assert result["result"]["take_profit_algo_id"] == 502


def test_binance_reconciliation_keeps_unknown_when_protection_cannot_be_verified(monkeypatch):
    set_demo_env(monkeypatch)
    client = FakeBinanceClient()
    client.reconcile_algo_status = "CANCELED"
    adapter = BinanceFuturesOMSAdapter(client=client, venue_agent=FakeAgent())
    stored = {
        "client_order_id": "stx-binance-oms-001",
        "status": "UNKNOWN",
        "payload": {
            "venue": "binance", "environment": "binance_demo", "symbol": "BTCUSDT",
            "side": "BUY", "order_type": "MARKET", "quantity": 0.105,
            "stop_loss": 95.0, "take_profit": 110.0,
            "client_order_id": "stx-binance-oms-001",
        },
    }
    result = adapter.reconcile_intent(stored)
    assert result["confirmed"] is False
    assert result["status"] == "UNKNOWN"
    assert "not both confirmed working" in result["evidence"]