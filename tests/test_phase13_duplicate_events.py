"""
tests/test_stage13_duplicate_events.py
Stage 13.9-13.10: Idempotency and out-of-order event tests.
"""
import uuid

from paper_engine.portfolio import PaperPortfolio


def _fresh_portfolio(tmp_path, name="port.json"):
    path = str(tmp_path / name)
    return PaperPortfolio(filename=path)


# ─────────────────────────────────────────────────────────────
# 13.9 — DUPLICATE EVENT IDEMPOTENCY
# ─────────────────────────────────────────────────────────────

def test_duplicate_realized_pnl_event_idempotent(tmp_path):
    """
    The same PnL event replayed twice must only be applied once.
    """
    p = _fresh_portfolio(tmp_path)
    initial_cash = p.cash
    ev = str(uuid.uuid4())
    p.add_realized_pnl(500.0, ev)
    p.add_realized_pnl(500.0, ev)  # duplicate
    assert p.cash == initial_cash + 500.0
    assert p.realized_pnl == 500.0


def test_duplicate_margin_allocation_idempotent(tmp_path):
    """Allocating margin with the same event_id twice must only deduct once."""
    p = _fresh_portfolio(tmp_path)
    initial_cash = p.cash
    ev = str(uuid.uuid4())
    p.allocate_margin(1000.0, ev)
    p.allocate_margin(1000.0, ev)  # duplicate
    assert p.cash == initial_cash - 1000.0
    assert p.used_margin == 1000.0


def test_duplicate_margin_release_idempotent(tmp_path):
    """Releasing margin with the same event_id twice must only release once."""
    p = _fresh_portfolio(tmp_path)
    ev_alloc = str(uuid.uuid4())
    ev_rel = str(uuid.uuid4())
    p.allocate_margin(1000.0, ev_alloc)
    p.release_margin(1000.0, ev_rel)
    p.release_margin(1000.0, ev_rel)  # duplicate
    assert p.used_margin == 0.0
    assert p.cash == p.starting_capital


def test_duplicate_add_position_idempotent(tmp_path):
    """Adding a position with the same pos_id twice must not create duplicates."""
    p = _fresh_portfolio(tmp_path)
    pos_id = str(uuid.uuid4())
    p.add_position(pos_id, "BTCUSDT", "LONG", 50000.0, 0.001)
    p.add_position(pos_id, "BTCUSDT", "LONG", 51000.0, 0.002)  # duplicate
    pos = p.positions[pos_id]
    assert pos["entry_price"] == 50000.0  # first write wins
    assert pos["quantity"] == 0.001


def test_replayed_sequence_produces_same_result(tmp_path):
    """
    Replaying the same event sequence multiple times must produce identical state.
    Idempotency invariant: f(f(x)) == f(x) for all event sequences.
    """
    p = _fresh_portfolio(tmp_path)
    ev1 = str(uuid.uuid4())
    ev2 = str(uuid.uuid4())
    p.add_realized_pnl(200.0, ev1)
    p.add_realized_pnl(-50.0, ev2)
    cash_after_first_pass = p.cash

    # Replay the same events
    p.add_realized_pnl(200.0, ev1)
    p.add_realized_pnl(-50.0, ev2)
    assert p.cash == cash_after_first_pass


# ─────────────────────────────────────────────────────────────
# 13.10 — OUT-OF-ORDER EVENT HANDLING
# ─────────────────────────────────────────────────────────────

def test_out_of_order_events_do_not_double_count(tmp_path):
    """
    Events A, C, B (out of order) must not double-count if B arrives late.
    Since our portfolio uses event_id idempotency, late re-delivery is safe.
    """
    p = _fresh_portfolio(tmp_path)
    ev_a = str(uuid.uuid4())
    ev_b = str(uuid.uuid4())
    ev_c = str(uuid.uuid4())

    # Order: A, C, then B arrives late
    p.add_realized_pnl(100.0, ev_a)   # A
    p.add_realized_pnl(300.0, ev_c)   # C (out of order)
    p.add_realized_pnl(200.0, ev_b)   # B (late but new event_id)
    p.add_realized_pnl(200.0, ev_b)   # B replayed — must be idempotent

    expected = p.starting_capital + 100.0 + 300.0 + 200.0
    assert p.cash == expected


def test_signals_deduplication_across_replay():
    """SignalLogger must deduplicate signal IDs on replay."""
    import os
    import tempfile

    from paper_engine.signal_logger import SignalLogger

    with tempfile.NamedTemporaryFile(suffix=".jsonl", delete=False) as tmp:
        path = tmp.name

    try:
        logger = SignalLogger(path)
        sig_id = str(uuid.uuid4())

        # Emit same signal twice
        r1 = logger.log_signal({"signal_id": sig_id, "confidence": 0.8, "symbol": "BTCUSDT"})
        r2 = logger.log_signal({"signal_id": sig_id, "confidence": 0.8, "symbol": "BTCUSDT"})

        assert r1 is True   # first write succeeds
        assert r2 is False  # duplicate rejected

        with open(path) as f:
            lines = [l for l in f if l.strip()]
        assert len(lines) == 1
    finally:
        os.remove(path)
