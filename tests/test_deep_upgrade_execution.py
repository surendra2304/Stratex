from decimal import Decimal

from stratex_upgrade.execution import ExecutionEngine, PaperExecutionAdapter
from stratex_upgrade.models import OrderIntent, Side, Signal


def test_idempotent_submit():
    adapter = PaperExecutionAdapter()
    engine = ExecutionEngine(adapter)
    signal = Signal("sig", "strat", "BTCUSDT", Side.BUY, 1, "5m")
    intent = OrderIntent.create(signal, Decimal("0.1"))
    a = engine.submit(intent)
    b = engine.submit(intent)
    assert a.order_id == b.order_id
    assert len(engine.tracked_orders()) == 1


def test_paper_fill():
    adapter = PaperExecutionAdapter()
    engine = ExecutionEngine(adapter)
    signal = Signal("sig2", "strat", "BTCUSDT", Side.BUY, 1, "5m")
    intent = OrderIntent.create(signal, Decimal("0.1"))
    order = engine.submit(intent)
    fill = adapter.fill_at(order.order_id, Decimal(100))
    updated = engine.apply_fill(fill)
    assert str(updated.status) == "OrderStatus.FILLED"
