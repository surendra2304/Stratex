from decimal import Decimal

from stratex_upgrade.backtest import (
    BacktestConfig,
    BacktestOrder,
    Bar,
    EventDrivenBacktester,
)


def test_no_lookahead_entry_is_next_bar():
    bars = [Bar(i, Decimal(100), Decimal(110), Decimal(90), Decimal(100)) for i in range(4)]
    def signal(window):
        if len(window) == 1:
            return BacktestOrder(1, "BUY", Decimal(1), Decimal(100), Decimal(95), Decimal(110), "s", "x")
        return None
    bt = EventDrivenBacktester(bars, BacktestConfig(initial_equity=Decimal(1000), fee_bps=0, slippage_bps=0))
    trades = bt.run(signal)
    assert trades
    assert trades[0].entry_timestamp == 1


def test_conservative_intrabar_hits_sl_first():
    bars = [
        Bar(0, Decimal(100), Decimal(100), Decimal(100), Decimal(100)),
        Bar(1, Decimal(100), Decimal(110), Decimal(90), Decimal(100)),
    ]
    def signal(window):
        if len(window) == 1:
            return BacktestOrder(1, "BUY", Decimal(1), Decimal(100), Decimal(95), Decimal(105), "s", "x")
        return None
    bt = EventDrivenBacktester(bars, BacktestConfig(initial_equity=Decimal(1000), fee_bps=0, slippage_bps=0, conservative_intrabar=True))
    trades = bt.run(signal)
    assert trades[0].exit_reason == "SL_HIT"
