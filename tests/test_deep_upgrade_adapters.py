from decimal import Decimal

from stratex_upgrade.adapters.freqtrade_ideas import ProtectionEvaluator
from stratex_upgrade.adapters.quantconnect_ideas import BuyingPowerGuard


def test_buying_power():
    r = BuyingPowerGuard().check(free_cash=Decimal(100), order_notional=Decimal(70), reserved=Decimal(20))
    assert r.sufficient


def test_protection_drawdown():
    blocked, reason = ProtectionEvaluator().blocked(now=0, last_loss_ns=None, drawdown=Decimal("0.10"), consecutive_losses=0)
    assert blocked
    assert reason == "MAX_DRAWDOWN"
