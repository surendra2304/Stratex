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
        self.open_algo_orders = []
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
        return getattr(self, "ticker_response", {"symbol": symbol, "price": "100.0"})

    def futures_account(self):
        return getattr(self, "account_response", {"availableBalance": "10000"})

    def futures_position_information(self, symbol):
        return [{"symbol": symbol, "positionSide": "BOTH", "positionAmt": self.position_amount}]

    def futures_get_open_algo_orders(self, symbol):
        return list(self.open_algo_orders)

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
        is_stop = clientAlgoId.startswith("stxsl-")
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

def test_binance_adapter_blocks_new_entry_when_stale_stratex_algo_order_remains(monkeypatch):
    set_demo_env(monkeypatch)
    client = FakeBinanceClient(hedge_mode=False)
    client.open_algo_orders = [{
        "symbol": "BTCUSDT", "algoId": 1001,
        "clientAlgoId": "stxtp-0123456789012345678901",
        "algoStatus": "NEW",
    }]
    adapter = BinanceFuturesOMSAdapter(client=client, venue_agent=FakeAgent())
    with pytest.raises(BinanceVenueError, match="Outstanding protective Algo Order"):
        adapter.submit_intent(make_intent())
    assert client.order_calls == []
    assert client.algo_calls == []

def test_binance_adapter_blocks_generic_open_close_position_protection(monkeypatch):
    set_demo_env(monkeypatch)
    client = FakeBinanceClient(hedge_mode=False)
    client.open_algo_orders = [{
        "symbol": "BTCUSDT", "algoId": 2002,
        "clientAlgoId": "manual-protection-123",
        "orderType": "STOP_MARKET", "closePosition": "true", "algoStatus": "NEW",
    }]
    adapter = BinanceFuturesOMSAdapter(client=client, venue_agent=FakeAgent())
    with pytest.raises(BinanceVenueError, match="Outstanding protective Algo Order"):
        adapter.submit_intent(make_intent())
    assert client.order_calls == []


def test_binance_reconciliation_does_not_resolve_cancelled_entry_with_partial_fill(monkeypatch):
    set_demo_env(monkeypatch)
    client = FakeBinanceClient()
    client.reconcile_entry = {"status": "CANCELED", "orderId": 111, "avgPrice": "100", "executedQty": "0.025"}
    adapter = BinanceFuturesOMSAdapter(client=client, venue_agent=FakeAgent())
    stored = {
        "client_order_id": "stx-binance-oms-001", "status": "UNKNOWN",
        "payload": {
            "venue": "binance", "environment": "binance_demo", "symbol": "BTCUSDT",
            "side": "BUY", "order_type": "MARKET", "quantity": 0.105,
            "stop_loss": 95.0, "take_profit": 110.0, "client_order_id": "stx-binance-oms-001",
        },
    }
    result = adapter.reconcile_intent(stored)
    assert result["confirmed"] is False
    assert result["status"] == "UNKNOWN"
    assert "partial_fill_requiring_protection_reconciliation" in result["evidence"]

def test_binance_limit_intent_pairs_with_working_sl_tp_algo_orders(monkeypatch):
    set_demo_env(monkeypatch)
    client = FakeBinanceClient(hedge_mode=True)
    # Simulate a resting limit order on the book
    client.futures_create_order = lambda **params: {
        "status": "NEW", "orderId": 777, "avgPrice": "0", "executedQty": "0",
        **params
    }
    client.futures_cancel_order = lambda **params: {"status": "CANCELED"}
    adapter = BinanceFuturesOMSAdapter(client=client, venue_agent=FakeAgent())

    intent = TradeIntent(
        venue="binance", symbol="BTCUSDT", side="BUY", order_type="LIMIT",
        quantity=0.105, limit_price=99.0, environment="binance_demo",
        stop_loss=95.0, take_profit=110.0, strategy="adapter-test",
        client_order_id="stx-limit-001",
    )
    result = adapter.submit_intent(intent)
    assert result["status"] == "ACCEPTED"
    assert result["order_type"] == "LIMIT"
    assert result["price"] == 99.0
    assert result["stop_algo_id"] == 1
    assert result["take_profit_algo_id"] == 2
    assert len(client.algo_calls) == 2
    assert client.algo_calls[0]["type"] == "STOP_MARKET"
    assert client.algo_calls[0]["triggerPrice"] == "95.0"
    assert client.algo_calls[1]["type"] == "TAKE_PROFIT_MARKET"
    assert client.algo_calls[1]["triggerPrice"] == "110.0"


def test_binance_limit_protection_failure_cancels_pending_limit_order(monkeypatch):
    set_demo_env(monkeypatch)
    client = FakeBinanceClient(hedge_mode=True, fail_take_profit=True)
    cancelled_orders = []
    client.futures_create_order = lambda **params: {
        "status": "NEW", "orderId": 777, "avgPrice": "0", "executedQty": "0",
        **params
    }
    client.futures_cancel_order = lambda **params: cancelled_orders.append(params) or {"status": "CANCELED"}
    adapter = BinanceFuturesOMSAdapter(client=client, venue_agent=FakeAgent())

    intent = TradeIntent(
        venue="binance", symbol="BTCUSDT", side="BUY", order_type="LIMIT",
        quantity=0.105, limit_price=99.0, environment="binance_demo",
        stop_loss=95.0, take_profit=110.0, strategy="adapter-test",
        client_order_id="stx-limit-fail-001",
    )
    result = adapter.submit_intent(intent)
    assert result["status"] == "REJECTED"
    assert "pending limit order was cancelled" in result["reason"]
    assert len(cancelled_orders) == 1
    assert cancelled_orders[0]["origClientOrderId"] == "stx-limit-fail-001"
    assert client.cancelled_algos == [{"symbol": "BTCUSDT", "algoId": 1}]


def test_hard_risk_limits_enforced_leverage_and_margin(monkeypatch):
    set_demo_env(monkeypatch)
    client = FakeBinanceClient()
    adapter = BinanceFuturesOMSAdapter(client=client, venue_agent=FakeAgent())

    # 1. Leverage > 5 rejected
    intent_high_lev = TradeIntent(
        venue="binance", symbol="BTCUSDT", side="BUY", order_type="MARKET",
        quantity=0.105, environment="binance_demo", stop_loss=95.0, take_profit=110.0,
        strategy="adapter-test", client_order_id="stx-risk-001",
        metadata={"leverage": "10"},
    )
    with pytest.raises(BinanceVenueError, match="max 5x leverage invariant"):
        adapter.submit_intent(intent_high_lev)

    # 2. Required margin > $1,000 rejected
    # quantity = 60 BTC @ 100 USDT = $6,000 / 5 = $1,200 margin > $1,000
    intent_high_margin = TradeIntent(
        venue="binance", symbol="BTCUSDT", side="BUY", order_type="MARKET",
        quantity=60.0, environment="binance_demo", stop_loss=95.0, take_profit=110.0,
        strategy="adapter-test", client_order_id="stx-risk-002",
        metadata={"leverage": "5"},
    )
    with pytest.raises(BinanceVenueError, match=r"exceeds deterministic hard limit of \$1,000"):
        adapter.submit_intent(intent_high_margin)


def test_rounding_utilities_and_symbol_filter_errors(monkeypatch):
    from decimal import Decimal
    set_demo_env(monkeypatch)
    client = FakeBinanceClient()
    adapter = BinanceFuturesOMSAdapter(client=client, venue_agent=FakeAgent())

    # Step size <= 0
    q = adapter._round_quantity(1.234, Decimal("0"))
    assert q == Decimal("1.234")

    # Tick size <= 0
    p = adapter._round_price(100.55, Decimal("0"), rounding=None)
    assert p == Decimal("100.55")

    # Unknown symbol
    with pytest.raises(BinanceVenueError, match="is not listed"):
        adapter._market_rules("UNKNOWN_COIN")

    # Unsupported order type for Binance OMS adapter (e.g. STOP)
    bad_type_intent = TradeIntent(
        venue="binance", symbol="BTCUSDT", side="BUY", order_type="STOP",
        quantity=0.1, environment="binance_demo", stop_loss=95.0, take_profit=110.0,
        strategy="adapter-test", client_order_id="stx-bad-type-001",
    )
    with pytest.raises(BinanceVenueError, match="accepts MARKET and LIMIT entries only"):
        adapter.submit_intent(bad_type_intent)


def test_mismatched_client_environment_error(monkeypatch):
    set_demo_env(monkeypatch)
    client = FakeBinanceClient()
    client.stratex_environment = "wrong_environment"
    with pytest.raises(BinanceVenueError, match="environment is unverified or mismatched"):
        BinanceFuturesOMSAdapter(client=client, venue_agent=FakeAgent())


def test_market_entry_with_query_recheck(monkeypatch):
    set_demo_env(monkeypatch)
    client = FakeBinanceClient()
    # Mock initial entry as NEW, but subsequent get_order as FILLED
    client.market_response = {"orderId": 999, "status": "NEW", "executedQty": "0", "avgPrice": "0"}
    client.futures_get_order = lambda symbol, origClientOrderId: {
        "orderId": 999, "status": "FILLED", "executedQty": "0.105", "avgPrice": "100.0"
    }
    adapter = BinanceFuturesOMSAdapter(client=client, venue_agent=FakeAgent())
    intent = TradeIntent(
        venue="binance", symbol="BTCUSDT", side="BUY", order_type="MARKET",
        quantity=0.105, environment="binance_demo", stop_loss=95.0, take_profit=110.0,
        strategy="adapter-test", client_order_id="stx-recheck-001",
    )
    res = adapter.submit_intent(intent)
    assert res["status"] == "FILLED"
    assert res["order_id"] == "111"


def test_position_mode_and_assert_symbol_flat_errors(monkeypatch):
    set_demo_env(monkeypatch)
    client = FakeBinanceClient()

    # Position mode network failure
    def broken_mode():
        raise RuntimeError("mode check failed")
    client.futures_get_position_mode = broken_mode
    adapter = BinanceFuturesOMSAdapter(client=client, venue_agent=FakeAgent())
    with pytest.raises(BinanceVenueError, match="Cannot verify Binance Futures position mode"):
        adapter._position_mode()

    # Position mode bad dict
    client.futures_get_position_mode = lambda: {"something_else": 123}
    with pytest.raises(BinanceVenueError, match="invalid position-mode response"):
        adapter._position_mode()

    # Position info query failure
    def broken_positions(symbol):
        raise RuntimeError("pos info failed")
    client.futures_position_information = broken_positions
    with pytest.raises(BinanceVenueError, match="Cannot reconcile current Futures positions"):
        adapter._assert_symbol_flat("BTCUSDT")

    # Non-flat position
    client.futures_position_information = lambda symbol: [{"symbol": "BTCUSDT", "positionAmt": "0.5", "positionSide": "LONG"}]
    with pytest.raises(BinanceVenueError, match="Existing LONG position"):
        adapter._assert_symbol_flat("BTCUSDT")


def test_binance_adapter_validation_and_risk_branches(monkeypatch):
    set_demo_env(monkeypatch)
    client = FakeBinanceClient()
    adapter = BinanceFuturesOMSAdapter(client=client, venue_agent=FakeAgent())

    # 1. Non-binance venue
    intent_mt5 = TradeIntent(
        venue="mt5", symbol="BTCUSDT", side="BUY", order_type="MARKET",
        quantity=0.1, environment="mt5_demo", stop_loss=95.0, take_profit=110.0,
        strategy="adapter-test", client_order_id="stx-venue-001",
    )
    with pytest.raises(BinanceVenueError, match="different venue"):
        adapter.submit_intent(intent_mt5)

    # 2. Quantity below min_qty
    intent_tiny_qty = TradeIntent(
        venue="binance", symbol="BTCUSDT", side="BUY", order_type="MARKET",
        quantity=0.0001, environment="binance_demo", stop_loss=95.0, take_profit=110.0,
        strategy="adapter-test", client_order_id="stx-tiny-001",
    )
    with pytest.raises(BinanceVenueError, match="minimum quantity"):
        adapter.submit_intent(intent_tiny_qty)

    # 3. LIMIT with missing limit_price
    intent_limit_no_price = TradeIntent(
        venue="binance", symbol="BTCUSDT", side="BUY", order_type="LIMIT",
        quantity=0.1, environment="binance_demo", stop_loss=95.0, take_profit=110.0,
        strategy="adapter-test", client_order_id="stx-limit-noprice-001",
    )
    with pytest.raises(BinanceVenueError, match="positive limit_price"):
        adapter.submit_intent(intent_limit_no_price)

    # 4. Long LIMIT with inverted bracket (stop_loss > limit_price)
    intent_long_bad_bracket = TradeIntent(
        venue="binance", symbol="BTCUSDT", side="BUY", order_type="LIMIT",
        quantity=0.1, environment="binance_demo", limit_price=90.0, stop_loss=95.0, take_profit=110.0,
        strategy="adapter-test", client_order_id="stx-limit-badbracket-001",
    )
    with pytest.raises(BinanceVenueError, match="For a long LIMIT entry"):
        adapter.submit_intent(intent_long_bad_bracket)

    # 5. Short LIMIT with inverted bracket (take_profit > limit_price)
    intent_short_bad_bracket = TradeIntent(
        venue="binance", symbol="BTCUSDT", side="SELL", order_type="LIMIT",
        quantity=0.1, environment="binance_demo", limit_price=90.0, stop_loss=105.0, take_profit=95.0,
        strategy="adapter-test", client_order_id="stx-short-badbracket-001",
    )
    with pytest.raises(BinanceVenueError, match="For a short LIMIT entry"):
        adapter.submit_intent(intent_short_bad_bracket)

    # 6. Notional below min_notional ($5.0)
    # quantity = 0.01 * price 100 = $1.0 < $5.0 min_notional
    intent_low_notional = TradeIntent(
        venue="binance", symbol="BTCUSDT", side="BUY", order_type="MARKET",
        quantity=0.01, environment="binance_demo", stop_loss=95.0, take_profit=110.0,
        strategy="adapter-test", client_order_id="stx-low-notional-001",
    )
    with pytest.raises(BinanceVenueError, match="minimum notional"):
        adapter.submit_intent(intent_low_notional)

    # 7. Insufficient available balance
    client.account_response = {"availableBalance": "1.00"}
    intent_big = TradeIntent(
        venue="binance", symbol="BTCUSDT", side="BUY", order_type="MARKET",
        quantity=0.2, environment="binance_demo", stop_loss=95.0, take_profit=110.0,
        strategy="adapter-test", client_order_id="stx-big-001",
    )
    with pytest.raises(BinanceVenueError, match="Insufficient verified available Futures margin"):
        adapter.submit_intent(intent_big)
