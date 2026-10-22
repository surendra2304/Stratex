from decimal import Decimal

from stratex_upgrade.models import InstrumentRules, OrderIntent, Side, Signal


def test_signal_fingerprint_stable():
    s = Signal("s1", "demo", "BTCUSDT", Side.BUY, 1, "5m", confidence=0.8)
    assert s.fingerprint() == s.fingerprint()


def test_order_intent_client_id_is_deterministic():
    s = Signal("s1", "demo", "BTCUSDT", Side.BUY, 1, "5m")
    a = OrderIntent.create(s, Decimal(1))
    b = OrderIntent.create(s, Decimal(1))
    assert a.client_order_id == b.client_order_id


def test_instrument_rules():
    r = InstrumentRules("BTCUSDT", Decimal("0.01"), Decimal("0.001"), Decimal("0.001"), Decimal(10))
    r.validate_price(Decimal("100.00"))
    r.validate_qty(Decimal("0.01"))
