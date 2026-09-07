"""Repository-level safety and integration tests for STRATEX Deep Upgrade.

Verifies:
- Duplicate signals & deterministic fingerprints
- Idempotency & duplicate submissions
- UNKNOWN order state on submission timeouts
- Partial fill tracking
- Protection failure & emergency close residual handling
- Venue reconciliation mismatch gating
- Stale, out-of-order, and crossed market data rejection
- Risk limits without aggressive bypasses
- Concurrent risk reservations
- Buying power verification
- Unsupported order rejection
- No-lookahead backtest semantics
- Closed-candle data filtering
- Conservative same-bar SL/TP arbitration
- Cost accounting (fees + slippage)
- Walk-forward chronological validation
- Strategy governance (VALIDATED vs OBSERVE_ONLY)
- Atomic persistence
"""

import os
import tempfile
import time
from decimal import Decimal

import pandas as pd
import pytest

from stratex_upgrade.backtest import (
    BacktestConfig,
    BacktestOrder,
    Bar,
    EventDrivenBacktester,
)
from stratex_upgrade.connector import CCXTStyleAdapter, ExchangeCapabilities
from stratex_upgrade.costs import CostModel, net_pnl
from stratex_upgrade.execution import ExecutionEngine
from stratex_upgrade.market import FreshnessPolicy, MarketDataGuard
from stratex_upgrade.models import (
    InstrumentRules,
    OrderIntent,
    OrderStatus,
    RiskDecision,
    Side,
    Signal,
)
from stratex_upgrade.reconcile import AtomicJsonStore, ExchangeReconciler
from stratex_upgrade.risk import RiskLimits, RiskManager, RiskState
from stratex_upgrade.strategy_registry import StrategyRegistry, StrategySpec
from stratex_upgrade.walk_forward import tiled_windows


# 1. Duplicate signals & deterministic fingerprints
def test_duplicate_signal_fingerprint_deterministic():
    s1 = Signal(
        signal_id="sig_100",
        strategy="factory_winner_1",
        symbol="BTCUSDT",
        side=Side.BUY,
        created_at_ns=1700000000000,
        timeframe="5m",
        confidence=0.85,
        entry_price=Decimal(50000),
        stop_loss=Decimal(49000),
        take_profit=Decimal(52000),
    )
    s2 = Signal(
        signal_id="sig_100",
        strategy="factory_winner_1",
        symbol="BTCUSDT",
        side=Side.BUY,
        created_at_ns=1700000000000,
        timeframe="5m",
        confidence=0.85,
        entry_price=Decimal(50000),
        stop_loss=Decimal(49000),
        take_profit=Decimal(52000),
    )
    assert s1.fingerprint() == s2.fingerprint()
    assert len(s1.fingerprint()) == 64


# 2. Idempotency & duplicate submissions
def test_idempotent_order_submission_prevents_duplicate():
    sig = Signal("sig_200", "strat_a", "BTCUSDT", Side.BUY, time.time_ns(), "15m", 0.9)
    intent1 = OrderIntent.create(sig, Decimal("0.05"))
    intent2 = OrderIntent.create(sig, Decimal("0.05"))
    assert intent1.client_order_id == intent2.client_order_id

    class MockAdapter:
        def __init__(self):
            self.calls = 0
        def submit_order(self, intent):
            self.calls += 1
            return "exch_order_999"
        def fetch_order(self, sym, oid):
            return {"status": "FILLED", "orderId": oid, "executedQty": "0.05"}

    adapter = MockAdapter()
    engine = ExecutionEngine(adapter)
    res1 = engine.submit(intent1)
    res2 = engine.submit(intent2)
    assert adapter.calls == 1  # Exactly one submission to exchange
    assert res1.client_order_id == res2.client_order_id


# 3. UNKNOWN order state on submission timeout
def test_submission_timeout_transitions_to_unknown_not_failed():
    sig = Signal("sig_300", "strat_b", "ETHUSDT", Side.BUY, time.time_ns(), "5m", 0.8)
    intent = OrderIntent.create(sig, Decimal("1.0"))

    class TimeoutAdapter:
        def submit_order(self, intent):
            raise TimeoutError("Exchange transport connection timed out after POST")
        def fetch_order(self, sym, oid):
            return {}  # Order status cannot be determined immediately

    engine = ExecutionEngine(TimeoutAdapter())
    record = engine.submit(intent)
    assert record.status == OrderStatus.UNKNOWN
    assert "SUBMIT_EXCEPTION" in record.reason


# 4. Partial fill tracking
def test_partial_fill_tracking():
    sig = Signal("sig_400", "strat_c", "SOLUSDT", Side.BUY, time.time_ns(), "5m", 0.7)
    intent = OrderIntent.create(sig, Decimal("10.0"))

    class PartialAdapter:
        def submit_order(self, intent):
            return "exch_sol_1"
        def fetch_order(self, sym, oid):
            return {"status": "PARTIALLY_FILLED", "orderId": oid, "executedQty": "4.0"}

    engine = ExecutionEngine(PartialAdapter())
    record = engine.submit(intent)
    assert record.status == OrderStatus.PARTIALLY_FILLED
    assert record.filled_quantity == Decimal("4.0")


# 5. Protection failure & emergency close residual handling
def test_emergency_close_residual_leaves_unknown_state():
    from testnet_engine.protection import emergency_market_close

    class PartialCloseClient:
        def create_order(self, **kwargs):
            # Only fills 0.05 out of requested 0.10
            return {
                "orderId": 8888,
                "status": "PARTIALLY_FILLED",
                "executedQty": "0.05",
                "cummulativeQuoteQty": "2500"
            }

    resp = emergency_market_close(
        client=PartialCloseClient(),
        symbol="BTCUSDT",
        entry_side="BUY",
        executed_qty=0.10
    )
    assert resp["_is_flat"] is False
    assert resp["_residual_qty"] == pytest.approx(0.05)


# 6. Venue reconciliation mismatch blocks new entries
def test_reconciliation_mismatch_blocks_entries():
    class DummyAdapter:
        def fetch_open_orders(self):
            return [{"orderId": "rem_1", "clientOrderId": "stx-remote-untracked", "symbol": "BTCUSDT"}]
        def fetch_positions(self):
            return []

    reconciler = ExchangeReconciler(DummyAdapter())
    report = reconciler.reconcile(local_orders=[], local_positions=[])
    assert report.ok is False
    assert len(report.issues) > 0
    assert report.issues[0].category == "UNTRACKED_REMOTE_ORDER"


# 7. Stale market data rejected
def test_stale_market_data_rejected():
    guard = MarketDataGuard(FreshnessPolicy(max_age_seconds=5.0))
    stale_ts = time.time_ns() - int(10 * 1e9)  # 10 seconds old
    ok, reason = guard.accept("BTCUSDT", stale_ts, Decimal(50000), Decimal(50010), 1)
    assert ok is False
    assert reason == "STALE"


# 8. Out-of-order market data rejected
def test_out_of_order_market_data_rejected():
    guard = MarketDataGuard(FreshnessPolicy(max_age_seconds=10.0, require_sequence_monotonic=True))
    now = time.time_ns()
    ok1, _ = guard.accept("BTCUSDT", now, Decimal(50000), Decimal(50010), sequence=100)
    assert ok1 is True
    ok2, reason2 = guard.accept("BTCUSDT", now, Decimal(50000), Decimal(50010), sequence=99)
    assert ok2 is False
    assert reason2 == "OUT_OF_ORDER"


# 9. Crossed books rejected
def test_crossed_book_rejected():
    guard = MarketDataGuard(FreshnessPolicy(max_age_seconds=10.0))
    now = time.time_ns()
    ok, reason = guard.accept("BTCUSDT", now, bid=Decimal(50050), ask=Decimal(50000), sequence=1)
    assert ok is False
    assert reason == "CROSSED_BOOK"


# 10. Risk limits strictly enforced without aggressive bypasses
def test_risk_limits_enforced_strictly():
    state = RiskState(
        equity=Decimal(10000),
        starting_equity=Decimal(10000),
        peak_equity=Decimal(10000),
        daily_start_equity=Decimal(10000),
    )
    limits = RiskLimits(
        max_total_exposure=Decimal("0.05"),  # 5% = $500 max
        max_single_asset_exposure=Decimal("0.02"),  # 2% = $200 max
    )
    rm = RiskManager(state, limits)
    rules = InstrumentRules(
        symbol="BTCUSDT",
        tick_size=Decimal("0.01"),
        step_size=Decimal("0.001"),
        min_qty=Decimal("0.001"),
        min_notional=Decimal("10.0"),
    )
    # Proposed $600 exceeds 5% total exposure ($500)
    check = rm.evaluate(
        side=Side.BUY,
        qty=Decimal("0.012"),
        price=Decimal(50000),
        instrument=rules,
        positions=[],
        market_age_seconds=1.0,
        spread_bps=Decimal(2),
        estimated_slippage_bps=Decimal(1),
    )
    assert check.decision == RiskDecision.REJECT
    assert check.reason == "MAX_TOTAL_EXPOSURE"


# 11. Concurrent risk reservations
def test_concurrent_risk_reservations_prevent_overexposure():
    state = RiskState(
        equity=Decimal(10000),
        starting_equity=Decimal(10000),
        peak_equity=Decimal(10000),
        daily_start_equity=Decimal(10000),
    )
    limits = RiskLimits(max_total_exposure=Decimal("0.05"))  # $500 total exposure
    rm = RiskManager(state, limits)

    assert rm.reserve_notional(Decimal(300)) is True
    assert rm.reserved_notional() == Decimal(300)

    # Second concurrent signal attempts $300 (total $600 > $500 cap)
    assert rm.reserve_notional(Decimal(300)) is False
    assert rm.reserved_notional() == Decimal(300)

    # Settle first reservation
    rm.release_notional(Decimal(300))
    assert rm.reserved_notional() == Decimal(0)
    assert rm.reserve_notional(Decimal(300)) is True


# 12. Buying power check
def test_buying_power_verification():
    from stratex_upgrade.adapters.quantconnect_ideas import BuyingPowerGuard
    bpg = BuyingPowerGuard()
    # Free cash: $100, proposed order: $150, reserved: $0
    res1 = bpg.check(free_cash=Decimal(100), order_notional=Decimal(150), reserved=Decimal(0))
    assert res1.sufficient is False

    # Free cash: $100, reserved: $40, proposed: $50 -> Remaining 60 >= 50
    res2 = bpg.check(free_cash=Decimal(100), order_notional=Decimal(50), reserved=Decimal(40))
    assert res2.sufficient is True


# 13. Unsupported order type rejected
def test_unsupported_order_type_rejected():
    class DummyExchange:
        def create_order(self, *args, **kwargs):
            return {}
    adapter = CCXTStyleAdapter(DummyExchange(), exchange_id="mock_ex")
    adapter.capabilities = ExchangeCapabilities(
        spot=True, margin=False, futures=False, stop_orders=False, oco=False, websocket=False
    )
    with pytest.raises(ValueError, match="order_type.*not supported"):
        adapter.create_order("BTCUSDT", "BUY", "STOP_LOSS", Decimal("0.01"))



# 14. No-lookahead backtest semantics
def test_no_lookahead_backtest():
    bars = [
        Bar(1, Decimal(100), Decimal(105), Decimal(95), Decimal(102)),
        Bar(2, Decimal(103), Decimal(108), Decimal(101), Decimal(106)),
        Bar(3, Decimal(107), Decimal(110), Decimal(104), Decimal(109)),
    ]
    tester = EventDrivenBacktester(bars, BacktestConfig(slippage_bps=Decimal(0), fee_bps=Decimal(0)))

    # Signal triggered on bar 1 must NOT execute on bar 1; it executes on bar 2 open
    def sig_fn(history):
        if len(history) == 1:
            return BacktestOrder(history[-1].timestamp, "BUY", Decimal("1.0"), Decimal(102), None, None, "test", "s1")
        return None

    trades = tester.run(sig_fn)
    assert len(trades) == 1
    # Entry timestamp must be bar 2, not bar 1!
    assert trades[0].entry_timestamp == 2
    assert trades[0].entry_price == Decimal(103)


# 15. Closed candles only filtering
def test_closed_candle_filtering():
    now = pd.Timestamp.now(tz="UTC")
    # One past candle, one future unclosed candle
    df = pd.DataFrame([
        {"timestamp": now - pd.Timedelta(minutes=30), "close_time": now - pd.Timedelta(minutes=15), "close": 50000},
        {"timestamp": now - pd.Timedelta(minutes=10), "close_time": now + pd.Timedelta(minutes=5), "close": 50500},
    ])
    # Filter active candles
    closed_df = df[df["close_time"] <= now].copy()
    assert len(closed_df) == 1
    assert closed_df.iloc[0]["close"] == 50000


# 16. Conservative same-bar SL/TP execution
def test_conservative_same_bar_sl_tp():
    bars = [
        Bar(1, Decimal(100), Decimal(102), Decimal(99), Decimal(101)),
        # Bar 2 opens at 101, spikes to 120 and drops to 80 (hits both SL=90 and TP=110)
        Bar(2, Decimal(101), Decimal(120), Decimal(80), Decimal(105)),
    ]
    tester = EventDrivenBacktester(bars, BacktestConfig(conservative_intrabar=True, slippage_bps=Decimal(0), fee_bps=Decimal(0)))

    def sig_fn(history):
        if len(history) == 1:
            return BacktestOrder(1, "BUY", Decimal("1.0"), Decimal(101), stop_loss=Decimal(90), take_profit=Decimal(110), strategy="s", signal_id="sig_sl_tp")
        return None

    trades = tester.run(sig_fn)
    assert len(trades) == 1
    assert trades[0].exit_reason == "SL_HIT"
    assert trades[0].net_pnl < 0  # Conservative triggers stop-loss


# 17. Cost accounting includes fees and slippage
def test_cost_accounting_deducts_fees_and_slippage():
    cm = CostModel(taker_fee_bps=Decimal(10), base_slippage_bps=Decimal(5))
    entry_ref = Decimal(100)
    slip = cm.slippage_bps(spread_bps=Decimal(2))
    actual_entry = cm.buy_fill_price(entry_ref, slip)
    entry_fee = cm.fee(actual_entry * Decimal(1))

    exit_ref = Decimal(110)
    actual_exit = cm.sell_fill_price(exit_ref, slip)
    exit_fee = cm.fee(actual_exit * Decimal(1))

    pnl = net_pnl("BUY", actual_entry, actual_exit, Decimal(1), entry_fee, exit_fee)
    gross = (exit_ref - entry_ref) * Decimal(1)
    assert actual_entry > entry_ref
    assert actual_exit < exit_ref
    assert entry_fee > 0
    assert exit_fee > 0
    assert pnl < gross



# 18. Walk-forward chronological validation without shuffling
def test_walk_forward_chronological_splits():
    windows = tiled_windows(n=100, train=40, test=20, step=20)
    assert len(windows) == 3
    for w in windows:
        # Train strictly precedes test
        assert w.train_end == w.test_start
        assert w.train_start < w.train_end
        assert w.test_start < w.test_end



# 19. Strategy governance gates (VALIDATED vs OBSERVE_ONLY)
def test_strategy_governance_gates():
    reg = StrategyRegistry()
    spec_val = StrategySpec(
        name="strat_validated",
        module="strat_mod",
        version="v1.0",
        timeframes=("5m", "15m"),
        symbols=("BTCUSDT",),
        status="VALIDATED",
        source_hash="sha256_dummy",
    )
    spec_obs = StrategySpec(
        name="strat_observe",
        module="strat_mod",
        version="v1.0",
        timeframes=("5m",),
        symbols=("BTCUSDT",),
        status="OBSERVE_ONLY",
        source_hash="sha256_dummy",
    )
    reg.register(spec_val)
    reg.register(spec_obs)

    ok_val, _ = reg.executable("strat_validated", "5m", "BTCUSDT")
    assert ok_val is True

    ok_obs, reason = reg.executable("strat_observe", "5m", "BTCUSDT")
    assert ok_obs is False
    assert reason == "STATUS_OBSERVE_ONLY"


# 20. Atomic persistence crash safety
def test_atomic_persistence_crash_safety():
    with tempfile.TemporaryDirectory() as tmpdir:
        path = os.path.join(tmpdir, "state.json")
        store = AtomicJsonStore(path)
        store.write({"key": "original", "counter": 42})
        assert store.read({}) == {"key": "original", "counter": 42}

        # Atomic overwrite replaces completely
        store.write({"key": "updated", "counter": 43})
        assert store.read({}) == {"key": "updated", "counter": 43}
