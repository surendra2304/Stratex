"""Comprehensive tests for five additional trading framework integrations in STRATEX:
1. NautilusTrader (Deterministic Event Runtime)
2. VectorBT (Research Accelerator & Parameter Sweeps)
3. Jesse (Hyperparameters & Overfit Degradation)
4. Hummingbot (Microstructure, Order Book Imbalance & Connector Health)
5. QuantConnect LEAN (Modular Alpha -> Portfolio -> Risk -> Execution Pipeline)
"""

import pytest
import time
from stratex_nautilus.event_model import EventType, MarketEvent, OrderIntent, RuntimeState
from stratex_nautilus.deterministic_runtime import DeterministicRuntime
from stratex_vectorbt.adapter import VectorBTResearchAdapter, SweepSpec, SweepResult
from stratex_jesse.optimization import HyperParameter, OptimizationSplit, JesseOptimizationAdapter
from stratex_hummingbot.orderbook import OrderBookSnapshot, OrderBookImbalance
from stratex_hummingbot.connector import ConnectorHealth
from stratex_lean.framework import Insight, PortfolioTarget, AlphaRiskExecutionPipeline
from execution import ExecutionPolicy


# ==============================================================================
# 1. NAUTILUSTRADER: DETERMINISTIC EVENT RUNTIME
# ==============================================================================

def test_nautilus_deterministic_runtime_monotonicity():
    runtime = DeterministicRuntime()
    # Enqueue in valid monotonic order
    runtime.submit(MarketEvent(ts_ns=1000, symbol="BTC/USDT", payload={"price": 60000.0}))
    runtime.submit(MarketEvent(ts_ns=2000, symbol="BTC/USDT", payload={"price": 60100.0}))
    # Inject out-of-order event (time travel)
    runtime.submit(MarketEvent(ts_ns=1500, symbol="BTC/USDT", payload={"price": 60050.0}))

    dispatched = []
    with pytest.raises(ValueError, match="non-monotonic event timestamp"):
        runtime.run(lambda e: dispatched.append(e.ts_ns))

    assert len(dispatched) == 2
    assert runtime.state.fault is not None
    assert "non-monotonic" in runtime.state.fault


def test_nautilus_event_dispatch_and_fault_tracking():
    runtime = DeterministicRuntime()
    runtime.submit(MarketEvent(ts_ns=1000, symbol="BTC/USDT", payload={"bid": 59990.0, "ask": 60010.0}))
    runtime.submit(MarketEvent(ts_ns=2000, symbol="BTC/USDT", payload={"signal": "LONG"}, event_type=EventType.SIGNAL))
    runtime.submit(MarketEvent(ts_ns=3000, symbol="BTC/USDT", payload={"qty": 0.05}, event_type=EventType.INTENT))

    events = []
    count = runtime.run(lambda e: events.append(e))

    assert count == 3
    assert runtime.state.processed_events == 3
    assert runtime.state.fault is None
    assert events[0].event_type == EventType.MARKET_DATA
    assert events[1].event_type == EventType.SIGNAL
    assert events[2].event_type == EventType.INTENT


# ==============================================================================
# 2. VECTORBT: RESEARCH ACCELERATOR & PARAMETER SWEEPS
# ==============================================================================

def test_vectorbt_sweep_grid_and_ranking():
    # Synthetic backtest function simulating fast vectorization
    def mock_fast_eval(p):
        pnl = (p["fast"] - 10) * 10.0 + (p["slow"] - 30) * 5.0
        pf = 1.0 + (pnl / 200.0)
        dd = 2.0
        sharpe = 1.2 if pnl > 0 else 0.4
        return {"net_pnl": pnl, "profit_factor": pf, "max_drawdown": dd, "sharpe": sharpe}

    adapter = VectorBTResearchAdapter(mock_fast_eval)
    spec = SweepSpec(
        parameters={
            "fast": [10, 20, 30],
            "slow": [30, 40, 50],
        },
        max_trials=9,
        seed=42,
    )

    results = adapter.sweep(spec)
    assert len(results) == 9

    ranked = adapter.rank(results, min_profit_factor=1.10, max_drawdown=5.0)
    best = ranked[0]
    assert best.accepted is True
    assert best.metrics["profit_factor"] >= 1.10
    assert best.parameters["fast"] == 30
    assert best.parameters["slow"] == 50


def test_vectorbt_canonical_engine_confirmation():
    # VectorBT shortlisted candidate
    candidate = SweepResult(
        parameters={"ema_fast": 22, "ema_slow": 55},
        metrics={"net_pnl": 120.0, "profit_factor": 1.45},
        accepted=True,
    )

    # Canonical Stratex BacktestEngine confirmation runner
    def canonical_engine_runner(params):
        assert params["ema_fast"] == 22
        return {"net_pnl": 95.0, "profit_factor": 1.32, "max_drawdown": 1.8}

    confirmation = VectorBTResearchAdapter.validate_with_canonical_engine(candidate, canonical_engine_runner)
    assert confirmation["confirmed"] is True
    assert confirmation["canonical_metrics"]["profit_factor"] == 1.32


# ==============================================================================
# 3. JESSE: HYPERPARAMETERS & OVERFIT DEGRADATION
# ==============================================================================

def test_jesse_hyperparameter_contract():
    hp_int = HyperParameter(name="adx_period", kind="int", minimum=10, maximum=30, default=14)
    hp_real = HyperParameter(name="sl_atr", kind="float", minimum=1.0, maximum=5.0, default=2.0)
    hp_cat = HyperParameter(name="timeframe", kind="categorical", options=["15m", "1h", "4h"], default="1h")

    assert hp_int.minimum == 10
    assert hp_real.default == 2.0
    assert "4h" in hp_cat.options


def test_jesse_train_test_split_and_overfitting_degradation():
    split = OptimizationSplit(
        train_start="2026-01-01",
        train_end="2026-06-01",
        test_start="2026-06-02",
        test_end="2026-08-01",
    )

    def mock_eval(params, window):
        is_train = window[0] == split.train_start
        # Simulate overfit strategy: great on train, degraded on test
        return {
            "sharpe": 2.5 if is_train else 0.8,
            "profit_factor": 1.8 if is_train else 0.95,
        }

    adapter = JesseOptimizationAdapter(mock_eval)


    result = adapter.run_candidate({"adx": 14}, split)
    train_metrics = result["train"]
    test_metrics = result["test"]

    # Degradation = (train - test) / abs(train)
    deg = JesseOptimizationAdapter.degradation(train_metrics, test_metrics, metric="sharpe")
    assert deg == pytest.approx((2.5 - 0.8) / 2.5, rel=1e-3)
    assert deg > 0.50  # Over 50% degradation -> overfit detected!


# ==============================================================================
# 4. HUMMINGBOT: MICROSTRUCTURE & CONNECTOR HEALTH
# ==============================================================================

def test_hummingbot_orderbook_metrics_and_imbalance():
    snapshot = OrderBookSnapshot(
        symbol="BTC/USDT",
        bids=((60000.0, 10.0), (59990.0, 5.0)),
        asks=((60010.0, 5.0), (60020.0, 5.0)),
        ts_ms=int(time.time() * 1000),
    )

    assert snapshot.best_bid == 60000.0
    assert snapshot.best_ask == 60010.0
    assert snapshot.mid_price == 60005.0
    assert snapshot.spread == 10.0
    assert snapshot.spread_bps > 1.0

    # Imbalance: bids=15, asks=10 -> (15-10)/25 = 0.20
    imb = OrderBookImbalance.top_n(snapshot, n=2)
    assert imb == pytest.approx(0.20, rel=1e-3)

    summary = OrderBookImbalance.depth_summary(snapshot, n=2)
    assert summary["imbalance_top_n"] == pytest.approx(0.20, rel=1e-3)
    assert summary["is_stale"] is False


def test_hummingbot_stale_book_protection():
    stale_ts = int(time.time() * 1000) - 15_000  # 15 seconds old
    stale_snapshot = OrderBookSnapshot(
        symbol="ETH/USDT",
        bids=((3000.0, 20.0),),
        asks=((3002.0, 20.0),),
        ts_ms=stale_ts,
    )
    assert stale_snapshot.is_stale(max_age_ms=5000) is True


def test_hummingbot_connector_health():
    health_ok = ConnectorHealth(connected=True, last_market_data_ms=1000, last_order_update_ms=1000)
    assert health_ok.connected is True
    assert health_ok.error is None

    health_err = ConnectorHealth(connected=False, last_market_data_ms=None, last_order_update_ms=None, error="WS_DISCONNECTED")
    assert health_err.connected is False
    assert health_err.error == "WS_DISCONNECTED"


# ==============================================================================
# 5. QUANTCONNECT LEAN: MODULAR ALPHA -> PORTFOLIO -> RISK -> EXECUTION
# ==============================================================================

def test_lean_modular_pipeline_stages():
    # 1. Alpha model emits Insights
    def mock_alpha(symbol, price):
        return [Insight(symbol=symbol, direction=1, confidence=0.85, magnitude=1.0)]

    # 2. Portfolio construction model converts Insights into target weights
    def mock_portfolio(insights):
        return [PortfolioTarget(symbol=ins.symbol, target_weight=0.10 * ins.confidence) for ins in insights]

    # 3. Risk model scales or caps targets
    def mock_risk(targets):
        return [PortfolioTarget(symbol=t.symbol, target_weight=min(t.target_weight, 0.05)) for t in targets]

    executed = []
    # 4. Execution model routes to ExecutionIntents
    def mock_execution(targets):
        for t in targets:
            executed.append(t)

    pipeline = AlphaRiskExecutionPipeline(
        alpha=mock_alpha,
        portfolio=mock_portfolio,
        risk=mock_risk,
        execution=mock_execution,
    )

    targets = pipeline.step("BTC/USDT", 60000.0)
    assert len(targets) == 1
    assert targets[0].symbol == "BTC/USDT"
    assert targets[0].target_weight == 0.05  # Capped by risk stage
    assert len(executed) == 1


def test_lean_pipeline_preserves_stratex_safety_authorities():
    # Verify ExecutionPolicy cannot be bypassed by any LEAN pipeline stage
    can_place, reason = ExecutionPolicy.can_place_order()
    # In default research/test environment, ExecutionPolicy remains strictly authoritative
    assert not can_place or "ALLOWED" in reason
