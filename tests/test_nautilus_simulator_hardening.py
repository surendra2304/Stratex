"""Regression tests for execution-simulator input and fill integrity."""

import math

import pytest

from stratex_nautilus_adapter.models import OrderSide, OrderState, OrderType, Position, TimeInForce, TradeTick
from stratex_nautilus_adapter.orders import NautilusOrderManager
from stratex_nautilus_adapter.simulator import NautilusExecutionSimulator, SimulatorConfig, SlippageModel


def _tick(
    *,
    symbol="BTCUSDT",
    price=100.0,
    size=10.0,
    ts_event=1,
    side=OrderSide.BUY,
    trade_id="trade-1",
):
    return TradeTick(symbol=symbol, price=price, size=size, ts_event=ts_event, side=side, trade_id=trade_id)


@pytest.mark.parametrize(
    "kwargs,field",
    [
        ({"symbol": " "}, "tick symbol"),
        ({"price": 0.0}, "tick price"),
        ({"price": -1.0}, "tick price"),
        ({"price": float("nan")}, "tick price"),
        ({"price": float("inf")}, "tick price"),
        ({"size": 0.0}, "tick size"),
        ({"size": -1.0}, "tick size"),
        ({"size": float("nan")}, "tick size"),
        ({"size": float("inf")}, "tick size"),
        ({"ts_event": -1}, "ts_event"),
        ({"ts_event": True}, "ts_event"),
        ({"side": "BUY"}, "tick side"),
        ({"trade_id": ""}, "trade_id"),
    ],
)
def test_invalid_market_tick_is_rejected_before_order_or_position_mutation(kwargs, field):
    manager = NautilusOrderManager()
    simulator = NautilusExecutionSimulator(SimulatorConfig(slippage_model=SlippageModel.ZERO), manager)
    order = manager.create_order("BTCUSDT", OrderSide.BUY, OrderType.MARKET, 1.0)

    with pytest.raises(ValueError, match=field):
        simulator.process_tick(_tick(**kwargs))

    assert manager.get_order(order.client_order_id).state is OrderState.SUBMITTED
    assert simulator.list_fills() == []
    assert "BTCUSDT" not in simulator._positions


@pytest.mark.parametrize(
    "kwargs",
    [
        {"slippage_model": "not-a-model"},
        {"fixed_slippage_bps": -1.0},
        {"fixed_slippage_bps": 10000.0},
        {"fixed_slippage_bps": float("nan")},
        {"impact_constant": float("inf")},
        {"impact_constant": 100.0},
        {"taker_fee_pct": -0.1},
        {"taker_fee_pct": 1.0},
        {"maker_fee_pct": float("nan")},
        {"latency_ms": -1.0},
        {"latency_ms": True},
    ],
)
def test_simulator_config_rejects_unsafe_friction_values(kwargs):
    with pytest.raises(ValueError):
        SimulatorConfig(**kwargs)


def test_slippage_api_rejects_non_finite_or_invalid_inputs():
    simulator = NautilusExecutionSimulator(SimulatorConfig(slippage_model=SlippageModel.ZERO))

    for bad_price in (0.0, -1.0, float("nan"), float("inf")):
        with pytest.raises(ValueError, match="base_price"):
            simulator.calculate_slippage(bad_price, OrderSide.BUY, 1.0)
    with pytest.raises(ValueError, match="side"):
        simulator.calculate_slippage(100.0, "BUY", 1.0)
    with pytest.raises(ValueError, match="quantity"):
        simulator.calculate_slippage(100.0, OrderSide.BUY, float("nan"))


def test_limit_orders_receive_price_improvement_at_the_observed_trade_price():
    manager = NautilusOrderManager()
    simulator = NautilusExecutionSimulator(
        SimulatorConfig(slippage_model=SlippageModel.ZERO, maker_fee_pct=0.0),
        manager,
    )
    buy = manager.create_order(
        "BTCUSDT", OrderSide.BUY, OrderType.LIMIT, 1.0, price=110.0
    )
    buy_fills = simulator.process_tick(_tick(price=100.0, ts_event=10, trade_id="buy-fill"))
    assert len(buy_fills) == 1
    assert buy_fills[0].client_order_id == buy.client_order_id
    assert buy_fills[0].price == pytest.approx(100.0)
    assert manager.get_order(buy.client_order_id).state is OrderState.FILLED

    sell = manager.create_order(
        "BTCUSDT", OrderSide.SELL, OrderType.LIMIT, 1.0, price=90.0
    )
    sell_fills = simulator.process_tick(_tick(price=120.0, ts_event=11, trade_id="sell-fill"))
    assert len(sell_fills) == 1
    assert sell_fills[0].client_order_id == sell.client_order_id
    assert sell_fills[0].price == pytest.approx(120.0)


def test_duplicate_trade_id_is_idempotent_even_if_a_new_order_appears_later():
    manager = NautilusOrderManager()
    simulator = NautilusExecutionSimulator(SimulatorConfig(slippage_model=SlippageModel.ZERO), manager)
    first_tick = _tick(ts_event=10, trade_id="same-exchange-trade")

    assert simulator.process_tick(first_tick) == []
    order = manager.create_order("BTCUSDT", OrderSide.BUY, OrderType.MARKET, 1.0)

    assert simulator.process_tick(first_tick) == []
    assert manager.get_order(order.client_order_id).state is OrderState.SUBMITTED
    assert simulator.list_fills() == []

    fills = simulator.process_tick(_tick(ts_event=11, trade_id="next-exchange-trade"))
    assert len(fills) == 1
    assert manager.get_order(order.client_order_id).state is OrderState.FILLED


def test_out_of_order_market_data_is_rejected_without_filling_open_orders():
    manager = NautilusOrderManager()
    simulator = NautilusExecutionSimulator(SimulatorConfig(slippage_model=SlippageModel.ZERO), manager)
    assert simulator.process_tick(_tick(ts_event=100, trade_id="newer")) == []
    order = manager.create_order("BTCUSDT", OrderSide.BUY, OrderType.MARKET, 1.0)

    with pytest.raises(ValueError, match="timestamps must be non-decreasing"):
        simulator.process_tick(_tick(ts_event=99, trade_id="older"))

    assert manager.get_order(order.client_order_id).state is OrderState.SUBMITTED
    assert simulator.list_fills() == []


@pytest.mark.parametrize(
    "tick,quantity,expected_error",
    [
        (_tick(price=1e308, ts_event=1, trade_id="huge-notional"), 1e308, "fill notional must be finite"),
        (_tick(price=1.0, ts_event=2, trade_id="huge-position"), 1e308, "updated position quantity overflowed"),
    ],
)
def test_overflowing_fill_inputs_do_not_mutate_order_or_fill_ledger(tick, quantity, expected_error):
    manager = NautilusOrderManager()
    simulator = NautilusExecutionSimulator(SimulatorConfig(slippage_model=SlippageModel.ZERO), manager)
    if expected_error == "updated position quantity overflowed":
        simulator._positions["BTCUSDT"] = Position(
            symbol="BTCUSDT", quantity=1e308, avg_entry_price=100.0
        )
    order = manager.create_order("BTCUSDT", OrderSide.BUY, OrderType.MARKET, quantity)

    with pytest.raises(ValueError, match=expected_error):
        simulator.process_tick(tick)

    assert manager.get_order(order.client_order_id).state is OrderState.SUBMITTED
    assert simulator.list_fills() == []


def test_mutated_invalid_runtime_config_is_rejected_before_accepting_an_order():
    config = SimulatorConfig(slippage_model=SlippageModel.ZERO)
    manager = NautilusOrderManager()
    simulator = NautilusExecutionSimulator(config, manager)
    order = manager.create_order("BTCUSDT", OrderSide.BUY, OrderType.MARKET, 1.0)
    config.taker_fee_pct = math.inf

    with pytest.raises(ValueError, match="taker_fee_pct"):
        simulator.process_tick(_tick())

    assert manager.get_order(order.client_order_id).state is OrderState.SUBMITTED
    assert simulator.list_fills() == []


def test_non_marketable_ioc_limit_is_cancelled_instead_of_persisting():
    manager = NautilusOrderManager()
    simulator = NautilusExecutionSimulator(SimulatorConfig(slippage_model=SlippageModel.ZERO), manager)
    order = manager.create_order(
        "BTCUSDT", OrderSide.BUY, OrderType.LIMIT, 1.0, price=90.0, time_in_force=TimeInForce.IOC
    )

    assert simulator.process_tick(_tick(price=100.0, ts_event=10, trade_id="ioc-expire")) == []
    assert manager.get_order(order.client_order_id).state is OrderState.CANCELED

def test_oto_bracket_exits_cannot_execute_before_entry_fills():
    manager = NautilusOrderManager()
    simulator = NautilusExecutionSimulator(
        SimulatorConfig(slippage_model=SlippageModel.ZERO, maker_fee_pct=0.0, taker_fee_pct=0.0),
        manager,
    )
    bracket = manager.create_bracket_order(
        symbol="BTCUSDT",
        side=OrderSide.BUY,
        quantity=1.0,
        entry_price=100.0,
        take_profit_price=110.0,
        stop_loss_price=90.0,
    )

    # This price crosses the take-profit, but the buy-limit entry at 100 has
    # not filled. The exit must not create a naked short position.
    assert simulator.process_tick(_tick(price=111.0, ts_event=10, trade_id="tp-before-entry")) == []
    assert manager.get_order(bracket.entry_order.client_order_id).state is OrderState.ACCEPTED
    assert manager.get_order(bracket.take_profit_order.client_order_id).state is OrderState.SUBMITTED
    assert manager.get_order(bracket.stop_loss_order.client_order_id).state is OrderState.SUBMITTED
    assert "BTCUSDT" not in simulator._positions

    # Entry fills at 99; only now are both OTO exit children activated.
    entry_fills = simulator.process_tick(_tick(price=99.0, ts_event=11, trade_id="entry-fill"))
    assert len(entry_fills) == 1
    assert entry_fills[0].client_order_id == bracket.entry_order.client_order_id
    assert simulator.get_position("BTCUSDT").quantity == pytest.approx(1.0)
    assert manager.get_order(bracket.take_profit_order.client_order_id).state is OrderState.ACCEPTED
    assert manager.get_order(bracket.stop_loss_order.client_order_id).state is OrderState.ACCEPTED

    # The target can close the position only after the parent order is filled.
    exits = simulator.process_tick(_tick(price=111.0, ts_event=12, trade_id="tp-after-entry"))
    assert len(exits) == 1
    assert exits[0].client_order_id == bracket.take_profit_order.client_order_id
    assert manager.get_order(bracket.stop_loss_order.client_order_id).state is OrderState.CANCELED
    final_position = simulator.get_position("BTCUSDT")
    assert final_position.quantity == pytest.approx(0.0)
    assert final_position.realized_pnl == pytest.approx(12.0)
