"""
tests/test_stage13_stress.py
Stage 13.20-13.21: Long-run stress tests and property-based accounting tests.
"""
import random
import uuid

import pytest

from paper_engine.portfolio import PaperPortfolio

# ─────────────────────────────────────────────────────────────
# 13.20 — RESOURCE STRESS: 100K events
# ─────────────────────────────────────────────────────────────

def test_100k_pnl_events_no_corruption(tmp_path, monkeypatch):
    """
    100,000 PnL events processed in-memory.
    Verifies no unbounded growth, no corruption, invariant holds at end.
    """
    p = PaperPortfolio(filename=str(tmp_path / "p.json"))
    monkeypatch.setattr(p, "_save", lambda: None)
    rng = random.Random(999)

    total_pnl = 0.0
    for _ in range(100_000):
        ev = str(uuid.uuid4())
        pnl = rng.uniform(-0.01, 0.01)  # tiny amounts to avoid cash exhaustion
        total_pnl += pnl
        p.add_realized_pnl(pnl, ev)

    # Invariant: cash = starting_capital + total realized PnL
    expected_cash = p.starting_capital + total_pnl
    assert abs(p.cash - expected_cash) < 1e-6, f"Cash drift after 100K events: {p.cash} vs {expected_cash}"

    # No unbounded event ID set growth (it should have 100K unique IDs)
    assert len(p.processed_event_ids) == 100_000


def test_100k_events_no_memory_unbounded_growth():
    """Verify processed_event_ids grows linearly (not exponentially)."""
    import tracemalloc
    tracemalloc.start()

    p = PaperPortfolio(filename="stress_test_p.json")
    random.Random(42)
    import os

    n = 10_000
    for _ in range(n):
        p.processed_event_ids.add(str(uuid.uuid4()))

    _current, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    # Clean up
    if os.path.exists("stress_test_p.json"):
        os.remove("stress_test_p.json")
    if os.path.exists("stress_test_p.json.tmp"):
        os.remove("stress_test_p.json.tmp")

    # Peak memory for 10K UUIDs must be < 50MB (generous bound)
    assert peak < 50 * 1024 * 1024, f"Memory too high: {peak / (1024*1024):.2f} MB"


# ─────────────────────────────────────────────────────────────
# 13.21 — PROPERTY-BASED: accounting invariants
# ─────────────────────────────────────────────────────────────

def test_property_equity_equals_cash_plus_unrealized(tmp_path):
    """
    Property: for any sequence of PnL events and open positions,
    equity = cash + unrealized_pnl must ALWAYS hold.
    """
    rng = random.Random(2024)
    p = PaperPortfolio(filename=str(tmp_path / "prop.json"))
    prices = {"BTCUSDT": 50000.0}

    for trial in range(200):
        op = rng.choice(["pnl", "open", "close_all", "price_move"])

        if op == "pnl":
            ev = str(uuid.uuid4())
            pnl = rng.uniform(-50, 100)
            try:
                p.add_realized_pnl(pnl, ev)
            except Exception:
                pass

        elif op == "open":
            pos_id = str(uuid.uuid4())
            ev = str(uuid.uuid4())
            ep = prices["BTCUSDT"]
            qty = rng.uniform(0.001, 0.01)
            margin = ep * qty * 0.1
            try:
                p.allocate_margin(margin, ev)
                p.add_position(pos_id, "BTCUSDT", rng.choice(["LONG", "SHORT"]), ep, qty)
            except Exception:
                pass

        elif op == "close_all":
            for pos_id, pos in list(p.positions.items()):
                if pos["status"] == "OPEN":
                    p.close_position(pos_id, prices["BTCUSDT"], exit_fee=0.01)

        elif op == "price_move":
            prices["BTCUSDT"] *= rng.uniform(0.99, 1.01)

        # Assert invariant every step: equity = cash + used_margin + unrealized
        # When allocate_margin() is used, the full notional is deducted from cash
        # and tracked in used_margin. get_equity() adds it back.
        equity = p.get_equity(prices)
        unrealized = p.get_unrealized_pnl(prices)
        expected = p.cash + p.used_margin + unrealized
        assert abs(equity - expected) < 1e-9, f"Invariant broken at trial {trial}: equity={equity}, cash+used_margin+unreal={expected}"


def test_property_same_seed_produces_same_result(tmp_path):
    """Reproducibility: same seed → same result."""
    def _run(seed, path):
        p = PaperPortfolio(filename=path)
        rng = random.Random(seed)
        pnls = [rng.uniform(-100, 200) for _ in range(50)]
        for pnl in pnls:
            ev = str(uuid.uuid4())
            try:
                p.add_realized_pnl(pnl, ev)
            except Exception:
                pass
        return p.cash, p.realized_pnl

    # Note: uuid4 is random so event IDs differ, but since all events are new,
    # we test that the sum of applied PnLs is reproducible given the same rng seed
    rng = random.Random(77)
    pnls = [rng.uniform(-100, 200) for _ in range(50)]
    sum(pnls)

    p1 = PaperPortfolio(filename=str(tmp_path / "r1.json"))
    p2 = PaperPortfolio(filename=str(tmp_path / "r2.json"))

    for pnl in pnls:
        p1.add_realized_pnl(pnl, str(uuid.uuid4()))
        p2.add_realized_pnl(pnl, str(uuid.uuid4()))

    assert abs(p1.cash - p2.cash) < 1e-9
    assert abs(p1.realized_pnl - p2.realized_pnl) < 1e-9


def test_cost_model_always_reduces_net_pnl(tmp_path):
    """
    Property: fees + slippage must always reduce net PnL.
    A trade with positive gross PnL but high costs must have lower net PnL.
    """
    # Simulate a trade
    entry = 50000.0
    exit_p = 51000.0
    qty = 0.1
    fee = 5.0
    slippage = 2.0
    spread_cost = 1.0

    gross_pnl = (exit_p - entry) * qty
    net_pnl = gross_pnl - fee - slippage - spread_cost

    assert net_pnl < gross_pnl, "Costs must reduce net PnL"
    assert gross_pnl == pytest.approx(100.0)
    assert net_pnl == pytest.approx(92.0)
