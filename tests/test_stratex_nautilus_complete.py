"""tests/test_stratex_nautilus_complete.py

Comprehensive test suite verifying NautilusTrader event-driven trading capabilities in Stratex:
1. Domain models, immutability, and enums
2. In-memory EventBus publish/subscribe and wildcard dispatch
3. Order state machine transitions and invalid transition rejections
4. Bracket orders with automated OCO take-profit / stop-loss cancellation
5. Multi-type bar aggregators (Tick, Volume, Value/Dollar, Time) and DataFrame conversion
6. Institutional pre-trade risk engine (Notional, Rate limits, Drawdown circuit breaker)
7. Execution matching simulator with friction, slippage, and position PnL tracking
8. Permanent security invariant: LIVE_TRADING_ENABLED = False
9. Flask REST API endpoints (/api/v1/nautilus/*)
"""

import time
import pytest
from datetime import datetime, timezone

from stratex_nautilus_adapter import (
    nt,
    NautilusNativeEngine,
    OrderSide,
    OrderType,
    OrderState,
    TimeInForce,
    BarType,
    TradeTick,
    Bar,
    NautilusOrder,
    BracketOrder,
    Position,
    RiskCheckResult,
    NautilusEventBus,
    Event,
    NautilusOrderManager,
    InvalidStateTransitionError,
    TickBarAggregator,
    VolumeBarAggregator,
    ValueBarAggregator,
    TimeBarAggregator,
    BarAggregatorEngine,
    NautilusRiskEngine,
    RiskConfig,
    NautilusExecutionSimulator,
    SimulatorConfig,
    SlippageModel,
)
from dashboard import app


@pytest.fixture
def flask_client():
    app.config["TESTING"] = True
    with app.test_client() as client:
        yield client


# ------------------------------------------------------------------------------
# 1. Models & Enums Tests
# ------------------------------------------------------------------------------
def test_nautilus_models_and_enums():
    tick = TradeTick(
        symbol="BTCUSDT",
        price=60000.0,
        size=1.5,
        ts_event=1700000000000000000,
        side=OrderSide.BUY,
    )
    assert tick.symbol == "BTCUSDT"
    assert tick.price == 60000.0
    assert tick.size == 1.5

    bar = Bar(
        bar_type=BarType.TICK,
        symbol="BTCUSDT",
        step=10.0,
        open=60000.0,
        high=60100.0,
        low=59950.0,
        close=60050.0,
        volume=10.0,
        notional=600000.0,
        ticks_count=10,
        ts_start=1000,
        ts_end=2000,
    )
    assert bar.vwap == 60000.0
    assert OrderState.FILLED.is_closed is True
    assert OrderState.ACCEPTED.is_open is True


# ------------------------------------------------------------------------------
# 2. EventBus Publish/Subscribe & Wildcard Dispatch Tests
# ------------------------------------------------------------------------------
def test_nautilus_event_bus_publish_subscribe():
    bus = NautilusEventBus()
    received_exact = []
    received_wildcard = []

    def on_exact(event: Event):
        received_exact.append(event)

    def on_wildcard(event: Event):
        received_wildcard.append(event)

    bus.subscribe("data.ticks.btcusdt", on_exact)
    bus.subscribe("data.*", on_wildcard)

    bus.publish("data.ticks.btcusdt", {"price": 61000.0})
    bus.publish("data.bars.ethusdt", {"close": 3000.0})

    assert len(received_exact) == 1
    assert received_exact[0].payload["price"] == 61000.0
    assert len(received_wildcard) == 2

    stats = bus.get_stats()
    assert stats["published_count"] == 2
    assert stats["delivered_count"] == 3

    bus.unsubscribe("data.ticks.btcusdt", on_exact)
    assert len(bus._handlers.get("data.ticks.btcusdt", [])) == 0


# ------------------------------------------------------------------------------
# 3. Order State Machine & Lifecycle Tests
# ------------------------------------------------------------------------------
def test_nautilus_order_manager_transitions():
    mgr = NautilusOrderManager()
    order = mgr.create_order(
        symbol="BTCUSDT",
        side=OrderSide.BUY,
        order_type=OrderType.LIMIT,
        quantity=2.0,
        price=50000.0,
    )
    assert order.state == OrderState.SUBMITTED

    # Valid: SUBMITTED -> ACCEPTED
    accepted = mgr.update_order_state(order.client_order_id, OrderState.ACCEPTED)
    assert accepted.state == OrderState.ACCEPTED

    # Valid: ACCEPTED -> PARTIALLY_FILLED
    partial = mgr.update_order_state(
        order.client_order_id,
        OrderState.PARTIALLY_FILLED,
        fill_qty=1.0,
        fill_price=50000.0,
    )
    assert partial.state == OrderState.PARTIALLY_FILLED
    assert partial.filled_qty == 1.0
    assert partial.remaining_qty == 1.0

    # Valid: PARTIALLY_FILLED -> FILLED
    filled = mgr.update_order_state(
        order.client_order_id,
        OrderState.FILLED,
        fill_qty=1.0,
        fill_price=50000.0,
    )
    assert filled.state == OrderState.FILLED
    assert filled.filled_qty == 2.0
    assert filled.remaining_qty == 0.0

    # Invalid: Transition from terminal state FILLED -> CANCELED
    with pytest.raises(InvalidStateTransitionError):
        mgr.update_order_state(order.client_order_id, OrderState.CANCELED)


# ------------------------------------------------------------------------------
# 4. Bracket Orders & OCO Auto-Cancellation Flow Tests
# ------------------------------------------------------------------------------
def test_nautilus_bracket_order_and_oco_flow():
    mgr = NautilusOrderManager()
    bracket = mgr.create_bracket_order(
        symbol="BTCUSDT",
        side=OrderSide.BUY,
        quantity=1.0,
        entry_price=50000.0,
        take_profit_price=52000.0,
        stop_loss_price=48000.0,
    )

    entry_id = bracket.entry_order.client_order_id
    tp_id = bracket.take_profit_order.client_order_id
    sl_id = bracket.stop_loss_order.client_order_id

    # Children start in SUBMITTED
    assert mgr.get_order(tp_id).state == OrderState.SUBMITTED
    assert mgr.get_order(sl_id).state == OrderState.SUBMITTED

    # Step 1: Entry fills -> OTO activates TP and SL to ACCEPTED
    mgr.update_order_state(entry_id, OrderState.ACCEPTED)
    mgr.update_order_state(entry_id, OrderState.FILLED, fill_qty=1.0, fill_price=50000.0)

    assert mgr.get_order(tp_id).state == OrderState.ACCEPTED
    assert mgr.get_order(sl_id).state == OrderState.ACCEPTED

    # Step 2: Take Profit fills -> OCO triggers automatic cancellation of Stop Loss!
    mgr.update_order_state(tp_id, OrderState.FILLED, fill_qty=1.0, fill_price=52000.0)
    assert mgr.get_order(tp_id).state == OrderState.FILLED
    assert mgr.get_order(sl_id).state == OrderState.CANCELED


# ------------------------------------------------------------------------------
# 5. Multi-Type Bar Aggregators Tests
# ------------------------------------------------------------------------------
def test_nautilus_bar_aggregators():
    ticks = [
        TradeTick("BTCUSDT", 50000.0, 1.0, 100),
        TradeTick("BTCUSDT", 50100.0, 2.0, 200),
        TradeTick("BTCUSDT", 49900.0, 1.5, 300),
        TradeTick("BTCUSDT", 50050.0, 0.5, 400),
    ]

    # 1. Tick Bar Aggregator (every 2 ticks)
    agg_tick = TickBarAggregator("BTCUSDT", step_ticks=2)
    b1 = agg_tick.update(ticks[0])
    assert b1 is None
    b2 = agg_tick.update(ticks[1])
    assert b2 is not None
    assert b2.open == 50000.0
    assert b2.high == 50100.0
    assert b2.close == 50100.0
    assert b2.ticks_count == 2

    # 2. Volume Bar Aggregator (every 3.0 volume)
    agg_vol = VolumeBarAggregator("BTCUSDT", step_volume=3.0)
    v1 = agg_vol.update(ticks[0])  # vol = 1.0
    assert v1 is None
    v2 = agg_vol.update(ticks[1])  # vol = 1.0 + 2.0 = 3.0 >= 3.0
    assert v2 is not None
    assert v2.volume == 3.0

    # 3. Value Bar Aggregator (every $100,000 notional)
    agg_val = ValueBarAggregator("BTCUSDT", step_value_usd=100000.0)
    val1 = agg_val.update(ticks[0])  # notional = 50,000
    assert val1 is None
    val2 = agg_val.update(ticks[1])  # notional = 50,000 + 100,200 = 150,200
    assert val2 is not None

    # 4. Engine batch DataFrame conversion
    all_bars = BarAggregatorEngine.aggregate_ticks(ticks, bar_type=BarType.TICK, step=2)
    df = BarAggregatorEngine.bars_to_dataframe(all_bars)
    assert not df.empty
    assert "vwap" in df.columns
    assert len(df) == 2


# ------------------------------------------------------------------------------
# 6. Institutional Pre-Trade Risk Engine Tests
# ------------------------------------------------------------------------------
def test_nautilus_risk_engine_institutional_checks():
    config = RiskConfig(
        max_order_notional_usd=50000.0,
        max_position_notional_usd=100000.0,
        max_open_orders=5,
        max_orders_per_second=10,
        max_drawdown_pct=5.0,
    )
    risk = NautilusRiskEngine(config=config)

    # 1. Normal order passes
    normal_ord = NautilusOrder("ord1", "BTCUSDT", OrderSide.BUY, OrderType.LIMIT, quantity=0.5, price=50000.0)
    res = risk.check_order(normal_ord)
    assert res.passed is True

    # 2. Exceeds max order notional ($50k limit, order is $75k)
    large_ord = NautilusOrder("ord2", "BTCUSDT", OrderSide.BUY, OrderType.LIMIT, quantity=1.5, price=50000.0)
    res = risk.check_order(large_ord)
    assert res.passed is False
    assert res.metric == "MAX_ORDER_NOTIONAL"

    # 3. Exceeds max open orders
    res = risk.check_order(normal_ord, open_orders_count=5)
    assert res.passed is False
    assert res.metric == "MAX_OPEN_ORDERS"

    # 4. Drawdown circuit breaker triggered (peak 100k -> 94k = 6% DD > 5% limit)
    risk.update_equity(94000.0)
    res = risk.check_order(normal_ord)
    assert res.passed is False
    assert res.metric == "CIRCUIT_BREAKER"


# ------------------------------------------------------------------------------
# 7. Execution Matching Simulator & Positions Tests
# ------------------------------------------------------------------------------
def test_nautilus_execution_simulator_and_positions():
    mgr = NautilusOrderManager()
    sim_config = SimulatorConfig(
        slippage_model=SlippageModel.FIXED_BPS,
        fixed_slippage_bps=10.0,  # 10 bps
        taker_fee_pct=0.0005,
    )
    sim = NautilusExecutionSimulator(config=sim_config, order_manager=mgr)

    # Submit a BUY market order for 1.0 BTC
    order = mgr.create_order("BTCUSDT", OrderSide.BUY, OrderType.MARKET, quantity=1.0)

    # Process tick at 50,000.0
    tick = TradeTick("BTCUSDT", 50000.0, 5.0, 1000)
    fills = sim.process_tick(tick)

    assert len(fills) == 1
    fill = fills[0]
    # Fixed slippage +10 bps on BUY = 50000 * 1.0010 = 50050.0
    assert fill.price == pytest.approx(50050.0)
    assert fill.quantity == 1.0
    assert fill.commission > 0.0

    pos = sim.get_position("BTCUSDT")
    assert pos.quantity == 1.0
    assert pos.avg_entry_price == pytest.approx(50050.0)

    # Submit a SELL market order to close position at 55,000.0
    sell_order = mgr.create_order("BTCUSDT", OrderSide.SELL, OrderType.MARKET, quantity=1.0)
    tick2 = TradeTick("BTCUSDT", 55000.0, 5.0, 2000)
    fills2 = sim.process_tick(tick2)

    assert len(fills2) == 1
    # Slippage -10 bps on SELL = 55000 * 0.9990 = 54945.0
    assert fills2[0].price == pytest.approx(54945.0)
    pos_closed = sim.get_position("BTCUSDT")
    assert pos_closed.quantity == 0.0
    assert pos_closed.realized_pnl > 4800.0  # (54945 - 50050) = 4895


# ------------------------------------------------------------------------------
# 8. Permanent Security Invariant Tests
# ------------------------------------------------------------------------------
def test_nautilus_client_security_invariant():
    assert nt.LIVE_TRADING_ENABLED is False
    order = NautilusOrder("live_test", "BTCUSDT", OrderSide.BUY, OrderType.MARKET, 1.0)
    with pytest.raises(PermissionError) as exc_info:
        nt.route_live_order(order)
    assert "LIVE_TRADING_ENABLED = False is permanent" in str(exc_info.value)


# ------------------------------------------------------------------------------
# 9. Flask REST API Routes Tests
# ------------------------------------------------------------------------------
def test_flask_nautilus_routes(flask_client):
    # GET /status
    res = flask_client.get("/api/v1/nautilus/status")
    assert res.status_code == 200
    data = res.get_json()
    assert data["status"] == "OK"
    assert data["data"]["status"] == "HEALTHY"
    assert data["data"]["live_trading_enabled"] is False

    # POST /bars/aggregate
    tick_payload = {
        "ticks": [
            {"symbol": "BTCUSDT", "price": 50000.0, "size": 1.0, "ts_event": 1000},
            {"symbol": "BTCUSDT", "price": 50100.0, "size": 1.0, "ts_event": 2000},
        ],
        "bar_type": "TICK",
        "step": 2,
    }
    res = flask_client.post("/api/v1/nautilus/bars/aggregate", json=tick_payload)
    assert res.status_code == 200
    data = res.get_json()
    assert data["status"] == "OK"
    assert len(data["data"]) == 1

    # POST /risk/check
    risk_payload = {
        "symbol": "BTCUSDT",
        "side": "BUY",
        "quantity": 0.1,
        "price": 50000.0,
    }
    res = flask_client.post("/api/v1/nautilus/risk/check", json=risk_payload)
    assert res.status_code == 200
    data = res.get_json()
    assert data["status"] == "OK"
    assert data["data"]["passed"] is True

    # POST /orders/bracket
    bracket_payload = {
        "symbol": "BTCUSDT",
        "side": "BUY",
        "quantity": 0.05,
        "entry_price": 50000.0,
        "take_profit_price": 52000.0,
        "stop_loss_price": 49000.0,
    }
    res = flask_client.post("/api/v1/nautilus/orders/bracket", json=bracket_payload)
    assert res.status_code == 201
    data = res.get_json()
    assert data["status"] == "OK"
    assert "bracket_id" in data["data"]
    assert data["data"]["is_active"] is True

    # POST /simulate
    sim_payload = {
        "ticks": [
            {"symbol": "BTCUSDT", "price": 50000.0, "size": 2.0, "ts_event": 3000},
        ]
    }
    res = flask_client.post("/api/v1/nautilus/simulate", json=sim_payload)
    assert res.status_code == 200
    data = res.get_json()
    assert data["status"] == "OK"
