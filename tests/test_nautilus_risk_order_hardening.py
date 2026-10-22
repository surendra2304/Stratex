"""Regression suite for fail-closed risk decisions and order lifecycle integrity."""

from concurrent.futures import ThreadPoolExecutor

import pytest

from stratex_nautilus_adapter.models import (
    NautilusOrder,
    OrderSide,
    OrderType,
    OrderState,
    Position,
)
from stratex_nautilus_adapter.orders import NautilusOrderManager
from stratex_nautilus_adapter.risk import NautilusRiskEngine, RiskConfig


def _order(
    *,
    order_id: str = "risk-order",
    side: OrderSide = OrderSide.BUY,
    order_type: OrderType = OrderType.LIMIT,
    quantity: float = 0.1,
    price: float | None = 100.0,
    stop_price: float | None = None,
) -> NautilusOrder:
    return NautilusOrder(
        client_order_id=order_id,
        symbol="BTCUSDT",
        side=side,
        order_type=order_type,
        quantity=quantity,
        price=price,
        stop_price=stop_price,
    )


def test_risk_engine_fails_closed_when_market_price_is_unknown():
    risk = NautilusRiskEngine()
    order = _order(order_type=OrderType.MARKET, quantity=100_000.0, price=None)

    result = risk.check_order(order)

    assert result.passed is False
    assert result.metric == "PRICE_UNAVAILABLE"
    assert len(risk._order_timestamps) == 0


def test_risk_engine_does_not_replace_an_explicit_invalid_price_with_an_estimate():
    risk = NautilusRiskEngine()
    order = _order(price=0.0)

    result = risk.check_order(order, estimated_price=100.0)

    assert result.passed is False
    assert result.metric == "INVALID_INPUT"
    assert len(risk._order_timestamps) == 0


@pytest.mark.parametrize("quantity", [0.0, -1.0, float("nan"), float("inf"), True, 10**400])
def test_risk_engine_rejects_invalid_order_quantities(quantity):
    result = NautilusRiskEngine().check_order(_order(quantity=quantity))

    assert result.passed is False
    assert result.metric == "INVALID_INPUT"


def test_risk_engine_rejects_non_finite_order_and_projected_position_notional():
    risk = NautilusRiskEngine()
    too_large = _order(quantity=1e308, price=1e308)
    assert risk.check_order(too_large).metric == "NOTIONAL_OVERFLOW"

    order = _order(quantity=1.0, price=100.0)
    position = Position(symbol="BTCUSDT", quantity=1e308, avg_entry_price=100.0)
    result = risk.check_order(order, current_position=position)
    assert result.passed is False
    assert result.metric == "POSITION_OVERFLOW"


def test_risk_engine_values_existing_position_at_current_risk_price():
    risk = NautilusRiskEngine(
        RiskConfig(
            max_order_notional_usd=10_000.0,
            max_position_notional_usd=2_000.0,
        )
    )
    order = _order(quantity=1.0, price=100.0)
    position = Position(symbol="BTCUSDT", quantity=20.0, avg_entry_price=10.0)

    result = risk.check_order(order, current_position=position)

    assert result.passed is False
    assert result.metric == "MAX_POSITION_NOTIONAL"
    assert result.value == pytest.approx(2_100.0)


def test_risk_engine_halts_after_invalid_equity_telemetry():
    risk = NautilusRiskEngine()
    with pytest.raises(ValueError, match="current_equity must be a finite real number"):
        risk.update_equity(float("nan"))

    result = risk.check_order(_order())
    assert result.passed is False
    assert result.metric == "CIRCUIT_BREAKER"


@pytest.mark.parametrize(
    "kwargs",
    [
        {"max_order_notional_usd": float("nan")},
        {"max_position_notional_usd": float("inf")},
        {"max_drawdown_pct": 0.0},
        {"max_open_orders": True},
        {"max_orders_per_second": 0},
    ],
)
def test_risk_config_rejects_invalid_thresholds(kwargs):
    with pytest.raises(ValueError):
        RiskConfig(**kwargs)


def test_risk_rate_limit_is_thread_safe():
    risk = NautilusRiskEngine(RiskConfig(max_orders_per_second=3))
    orders = [_order(order_id=f"parallel-{index}", price=None) for index in range(20)]

    def check(order):
        return risk.check_order(order, estimated_price=10.0)

    with ThreadPoolExecutor(max_workers=12) as pool:
        outcomes = list(pool.map(check, orders))

    assert sum(outcome.passed for outcome in outcomes) == 3
    assert sum(outcome.metric == "RATE_LIMIT" for outcome in outcomes) == 17


def test_order_manager_rejects_invalid_quantity_and_missing_required_prices():
    manager = NautilusOrderManager()
    with pytest.raises(ValueError, match="quantity must be positive"):
        manager.create_order("BTCUSDT", OrderSide.BUY, OrderType.MARKET, 0.0)

    with pytest.raises(ValueError, match="quantity must be a finite number"):
        manager.create_order("BTCUSDT", OrderSide.BUY, OrderType.MARKET, 10**400)

    with pytest.raises(ValueError, match="LIMIT orders require a positive price"):
        manager.create_order("BTCUSDT", OrderSide.BUY, OrderType.LIMIT, 1.0)

    with pytest.raises(ValueError, match="STOP_MARKET orders require a positive stop_price"):
        manager.create_order("BTCUSDT", OrderSide.SELL, OrderType.STOP_MARKET, 1.0)


def test_order_manager_does_not_overwrite_duplicate_client_order_ids():
    manager = NautilusOrderManager()
    original = manager.create_order(
        "BTCUSDT", OrderSide.BUY, OrderType.MARKET, 1.0, client_order_id="client-123"
    )

    with pytest.raises(ValueError, match="already exists"):
        manager.create_order(
            "BTCUSDT", OrderSide.SELL, OrderType.MARKET, 9.0, client_order_id="client-123"
        )

    assert manager.get_order("client-123") == original
    assert manager.get_order("client-123").quantity == 1.0


def test_order_manager_rejects_overfills_without_mutating_the_order():
    manager = NautilusOrderManager()
    order = manager.create_order("BTCUSDT", OrderSide.BUY, OrderType.LIMIT, 2.0, price=100.0)
    manager.update_order_state(order.client_order_id, OrderState.ACCEPTED)
    partial = manager.update_order_state(
        order.client_order_id, OrderState.PARTIALLY_FILLED, fill_qty=1.5, fill_price=100.0
    )

    with pytest.raises(ValueError, match="exceeds remaining quantity"):
        manager.update_order_state(
            order.client_order_id, OrderState.FILLED, fill_qty=0.6, fill_price=110.0
        )
    assert manager.get_order(order.client_order_id) == partial

    filled = manager.update_order_state(
        order.client_order_id, OrderState.FILLED, fill_qty=0.5, fill_price=110.0
    )
    assert filled.state is OrderState.FILLED
    assert filled.filled_qty == pytest.approx(2.0)
    assert filled.remaining_qty == 0.0
    assert filled.avg_fill_price == pytest.approx(102.5)


def test_order_manager_requires_a_complete_fill_before_marking_filled():
    manager = NautilusOrderManager()
    order = manager.create_order("BTCUSDT", OrderSide.BUY, OrderType.MARKET, 1.0)
    accepted = manager.update_order_state(order.client_order_id, OrderState.ACCEPTED)

    with pytest.raises(ValueError, match="fill the entire remaining quantity"):
        manager.update_order_state(order.client_order_id, OrderState.FILLED)

    assert manager.get_order(order.client_order_id) == accepted


def test_order_manager_requires_valid_fill_price_and_makes_state_notifications_idempotent():
    manager = NautilusOrderManager()
    order = manager.create_order("BTCUSDT", OrderSide.BUY, OrderType.MARKET, 1.0)
    accepted = manager.update_order_state(order.client_order_id, OrderState.ACCEPTED)
    assert manager.update_order_state(order.client_order_id, OrderState.ACCEPTED) == accepted

    with pytest.raises(ValueError, match="fill_price must be a finite number"):
        manager.update_order_state(
            order.client_order_id, OrderState.FILLED, fill_qty=1.0, fill_price=float("nan")
        )
    assert manager.get_order(order.client_order_id) == accepted


def test_order_manager_rejects_invalid_bracket_levels_before_creating_orders():
    manager = NautilusOrderManager()

    with pytest.raises(ValueError, match="bracket prices are inconsistent"):
        manager.create_bracket_order(
            symbol="BTCUSDT",
            side=OrderSide.BUY,
            quantity=1.0,
            entry_price=50_000.0,
            take_profit_price=48_000.0,
            stop_loss_price=52_000.0,
        )

    assert manager.list_orders() == []
    assert manager.list_brackets() == []

@pytest.mark.parametrize(
    "order_type,kwargs,reason",
    [
        (
            OrderType.STOP_LIMIT,
            {"price": 101.0, "stop_price": 100.0},
            "STOP_LIMIT is not safely supported",
        ),
        (
            OrderType.TRAILING_STOP,
            {"stop_price": 100.0},
            "TRAILING_STOP is unsupported",
        ),
    ],
)
def test_order_manager_rejects_order_types_without_a_complete_simulation(order_type, kwargs, reason):
    manager = NautilusOrderManager()

    with pytest.raises(ValueError, match=reason):
        manager.create_order("BTCUSDT", OrderSide.BUY, order_type, 1.0, **kwargs)

    assert manager.list_orders() == []

def test_oto_child_orders_are_not_ready_until_the_entry_is_filled():
    manager = NautilusOrderManager()
    bracket = manager.create_bracket_order(
        symbol="BTCUSDT",
        side=OrderSide.BUY,
        quantity=1.0,
        entry_price=100.0,
        take_profit_price=110.0,
        stop_loss_price=90.0,
    )

    assert manager.is_order_ready_for_matching(bracket.entry_order.client_order_id) is True
    assert manager.is_order_ready_for_matching(bracket.take_profit_order.client_order_id) is False
    assert manager.is_order_ready_for_matching(bracket.stop_loss_order.client_order_id) is False

    manager.update_order_state(bracket.entry_order.client_order_id, OrderState.ACCEPTED)
    manager.update_order_state(
        bracket.entry_order.client_order_id,
        OrderState.FILLED,
        fill_qty=1.0,
        fill_price=100.0,
    )

    assert manager.get_order(bracket.take_profit_order.client_order_id).state is OrderState.ACCEPTED
    assert manager.get_order(bracket.stop_loss_order.client_order_id).state is OrderState.ACCEPTED
    assert manager.is_order_ready_for_matching(bracket.take_profit_order.client_order_id) is True
    assert manager.is_order_ready_for_matching(bracket.stop_loss_order.client_order_id) is True
