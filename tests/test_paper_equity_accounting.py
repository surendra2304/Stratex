"""
Regression test for paper_engine/portfolio.py get_equity() accounting fix.

DEFECT (fixed): get_equity() returned cash + unrealized_pnl.
When positions are opened via allocate_margin(notional), the full notional is
deducted from cash and tracked in used_margin. The correct formula is:
    equity = cash + used_margin + unrealized_pnl
Without this correction, equity is understated by the total open-position
notional for the entire duration of every trade.
"""
import uuid
import pytest
from paper_engine.portfolio import PaperPortfolio

STARTING = 10_000.0


@pytest.fixture
def tmp_portfolio(tmp_path):
    return PaperPortfolio(
        filename=str(tmp_path / "port.json"),
        ledger_file=str(tmp_path / "ledger.jsonl"),
        equity_file=str(tmp_path / "equity.jsonl"),
    )


def test_equity_includes_used_margin_when_position_open(tmp_portfolio):
    """Equity must NOT drop by position notional when a position is opened."""
    p = tmp_portfolio
    entry_price, qty = 50_000.0, 0.001
    notional = entry_price * qty   # 50 USDT
    p.allocate_margin(notional, str(uuid.uuid4()))
    p.add_position(str(uuid.uuid4()), "BTCUSDT", "LONG", entry_price, qty)
    entry_fee = notional * 0.001
    p.add_realized_pnl(-entry_fee, str(uuid.uuid4()))

    equity = p.get_equity({"BTCUSDT": entry_price})
    expected = STARTING - entry_fee   # only fee deducted, no PnL
    assert abs(equity - expected) < 1e-6, (
        f"equity={equity:.6f} expected≈{expected:.6f}. "
        f"If equity≈{STARTING - notional - entry_fee:.6f}, used_margin fix is missing."
    )


def test_equity_reflects_unrealized_gain(tmp_portfolio):
    """Unrealized gain must be added correctly on top of the correct base."""
    p = tmp_portfolio
    entry_price, qty = 50_000.0, 0.001
    notional = entry_price * qty
    p.allocate_margin(notional, str(uuid.uuid4()))
    p.add_position(str(uuid.uuid4()), "BTCUSDT", "LONG", entry_price, qty)
    entry_fee = notional * 0.001
    p.add_realized_pnl(-entry_fee, str(uuid.uuid4()))

    current_price = entry_price * 1.02
    unrealized = (current_price - entry_price) * qty
    equity = p.get_equity({"BTCUSDT": current_price})
    assert abs(equity - (STARTING - entry_fee + unrealized)) < 1e-6


def test_equity_stable_after_close(tmp_portfolio):
    """Post-close equity reflects net PnL correctly."""
    p = tmp_portfolio
    entry_price, qty = 50_000.0, 0.001
    notional = entry_price * qty
    pos_id = str(uuid.uuid4())
    p.allocate_margin(notional, str(uuid.uuid4()))
    p.add_position(pos_id, "BTCUSDT", "LONG", entry_price, qty)
    entry_fee = notional * 0.001
    p.add_realized_pnl(-entry_fee, str(uuid.uuid4()))

    exit_price = 51_000.0
    exit_fee = exit_price * qty * 0.001
    gross_pnl = (exit_price - entry_price) * qty
    net_pnl = gross_pnl - exit_fee
    p.close_position(pos_id, exit_price, exit_fee=exit_fee)
    p.add_realized_pnl(net_pnl, str(uuid.uuid4()))

    equity = p.get_equity({})
    assert abs(equity - (STARTING - entry_fee + net_pnl)) < 1e-6


def test_equity_accurate_across_multiple_positions(tmp_portfolio):
    """Multiple concurrent open positions must all be accounted for."""
    p = tmp_portfolio
    ep1, qty1 = 50_000.0, 0.001
    p.allocate_margin(ep1 * qty1, str(uuid.uuid4()))
    p.add_position(str(uuid.uuid4()), "BTCUSDT", "LONG", ep1, qty1)
    f1 = ep1 * qty1 * 0.001
    p.add_realized_pnl(-f1, str(uuid.uuid4()))

    ep2, qty2 = 3_000.0, 0.01
    p.allocate_margin(ep2 * qty2, str(uuid.uuid4()))
    p.add_position(str(uuid.uuid4()), "ETHUSDT", "LONG", ep2, qty2)
    f2 = ep2 * qty2 * 0.001
    p.add_realized_pnl(-f2, str(uuid.uuid4()))

    btc_price, eth_price = ep1 * 1.01, ep2 * 0.995
    ur1 = (btc_price - ep1) * qty1
    ur2 = (eth_price - ep2) * qty2
    equity = p.get_equity({"BTCUSDT": btc_price, "ETHUSDT": eth_price})
    assert abs(equity - (STARTING - f1 - f2 + ur1 + ur2)) < 1e-6


def test_equity_no_double_counting_sequential_trades(tmp_portfolio):
    """Old margin must not accumulate across sequential trades causing overcounting."""
    p = tmp_portfolio
    ep1, qty1 = 40_000.0, 0.001
    pos1 = str(uuid.uuid4())
    p.allocate_margin(ep1 * qty1, str(uuid.uuid4()))
    p.add_position(pos1, "BTCUSDT", "LONG", ep1, qty1)
    f1_in = ep1 * qty1 * 0.001
    p.add_realized_pnl(-f1_in, str(uuid.uuid4()))
    f1_out = ep1 * qty1 * 0.001
    p.close_position(pos1, ep1, exit_fee=f1_out)
    p.add_realized_pnl(-f1_out, str(uuid.uuid4()))  # break-even exit

    ep2, qty2 = 41_000.0, 0.001
    pos2 = str(uuid.uuid4())
    p.allocate_margin(ep2 * qty2, str(uuid.uuid4()))
    p.add_position(pos2, "BTCUSDT", "LONG", ep2, qty2)
    f2_in = ep2 * qty2 * 0.001
    p.add_realized_pnl(-f2_in, str(uuid.uuid4()))

    equity = p.get_equity({"BTCUSDT": ep2})
    expected = STARTING - f1_in - f1_out - f2_in
    assert abs(equity - expected) < 1e-6, (
        f"equity={equity:.4f} expected≈{expected:.4f}. "
        f"Sequential used_margin may be double-counted."
    )
