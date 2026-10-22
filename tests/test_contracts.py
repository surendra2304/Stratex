from stratex_nautilus.deterministic_runtime import DeterministicRuntime
from stratex_nautilus.event_model import MarketEvent
from stratex_vectorbt.adapter import VectorBTResearchAdapter, SweepSpec
from stratex_jesse.optimization import JesseOptimizationAdapter, OptimizationSplit
from stratex_hummingbot.orderbook import OrderBookSnapshot, OrderBookImbalance


def test_deterministic_runtime_rejects_time_travel():
    r = DeterministicRuntime()
    r.submit(MarketEvent(2, "BTC/USDT", {}))
    r.submit(MarketEvent(1, "BTC/USDT", {}))
    try:
        r.run(lambda e: None)
    except ValueError:
        return
    assert False


def test_vectorbt_style_sweep():
    a = VectorBTResearchAdapter(lambda p: {"net_pnl": p["x"], "profit_factor": 1.5})
    out = a.sweep(SweepSpec({"x": [1, 2, 3]}, max_trials=2))
    assert len(out) == 2


def test_jesse_train_test_contract():
    def eval_fn(p, window):
        return {"sharpe": p["x"] + (1 if window[0] == "train" else 0)}
    a = JesseOptimizationAdapter(eval_fn)
    result = a.run_candidate({"x": 1}, OptimizationSplit("train", "t", "test", "u"))
    assert "train" in result and "test" in result


def test_orderbook_imbalance():
    s = OrderBookSnapshot("BTC/USDT", ((100, 10),), ((101, 5),), 1)
    assert OrderBookImbalance.top_n(s) == (10 - 5) / 15
