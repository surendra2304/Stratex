from decimal import Decimal

from stratex_upgrade.models import (
    InstrumentRules,
    RiskDecision,
    Side,
)
from stratex_upgrade.risk import RiskLimits, RiskManager, RiskState


def make_rm():
    return RiskManager(
        RiskState(Decimal(10000), Decimal(10000), Decimal(10000), Decimal(10000)),
        RiskLimits(max_open_positions=5),
    )


def test_position_size_respects_risk():
    rm = make_rm()
    rules = InstrumentRules("BTCUSDT", Decimal("0.01"), Decimal("0.001"), Decimal("0.001"), Decimal(10))
    qty = rm.calculate_qty(Decimal(100), Decimal(99), rules)
    assert qty > 0
    assert qty * Decimal(1) <= Decimal(50)


def test_risk_rejects_stale():
    rm = make_rm()
    rules = InstrumentRules("BTCUSDT", Decimal("0.01"), Decimal("0.001"), Decimal("0.001"), Decimal(10))
    check = rm.evaluate(
        side=Side.BUY,
        qty=Decimal("0.1"),
        price=Decimal(100),
        instrument=rules,
        positions=[],
        market_age_seconds=99,
        spread_bps=Decimal(1),
        estimated_slippage_bps=Decimal(1),
    )
    assert check.decision == RiskDecision.REJECT
