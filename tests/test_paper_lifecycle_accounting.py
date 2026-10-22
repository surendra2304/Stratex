"""Regression tests: paper-trading lifecycle accounting under repeated round trips.

Found by the long-simulation stress pass:
* paper_forward_runner allocated margin on entry but never released it, so after
  roughly 100% of capital in cumulative notional every later entry was rejected
  with "Insufficient cash" even with zero open positions;
* SL/TP lived only in an in-memory map, so a restarted runner could never exit
  positions opened before the restart;
* the kill-switch flatten leaked margin the same way;
* PaperABEngine reused one idempotency key for margin and PnL, silently dropping
  every fee and PnL booking (equity flat, drawdown guard blind), and counted
  CLOSED positions toward its concurrency cap (starved after 3 lifetime trades);
* close_position() re-recorded already-closed trades;
* the long-simulation harness never booked PnL and wrote into the working dir.
"""

import dataclasses
import json
import math
import os

import pandas as pd
import pytest

import paper_ab_runner as abr
import paper_forward_runner as pfr
from config_ab import get_default_ab_config
from paper_engine import kill_switch
from paper_engine.portfolio import PaperPortfolio


def _portfolio(tmp_path, name="pf.json"):
    return PaperPortfolio(filename=str(tmp_path / name))


def _ledger_rows(portfolio):
    if not os.path.exists(portfolio.ledger_file):
        return []
    with open(portfolio.ledger_file, encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def _tp_candle():
    return pd.DataFrame([{"open": 100.0, "high": 104.0, "low": 99.5, "close": 103.5}])


def test_forward_runner_round_trips_release_margin_and_keep_trading(tmp_path):
    pf = _portfolio(tmp_path)
    ledger = str(tmp_path / "ledger.jsonl")
    for i in range(40):
        res = pfr.paper_execute(pf, "BUY", "BTCUSDT", 100.0, 98.0, 103.0, {"BTCUSDT": 100.0}, ledger, signal_id=f"s{i}")
        assert res["status"] == "FILLED", (i, res)
        pfr.paper_exit_positions(pf, {"BTCUSDT": 103.5}, _tp_candle())

    assert all(p["status"] == "CLOSED" for p in pf.positions.values())
    assert pf.used_margin == pytest.approx(0.0, abs=1e-6)
    assert pf.cash == pytest.approx(pf.get_equity({"BTCUSDT": 103.5}))
    assert pf.cash + pf.used_margin == pytest.approx(pf.starting_capital + pf.realized_pnl)
    assert len(_ledger_rows(pf)) == 40


def test_forward_runner_persists_exit_levels_so_restart_can_exit(tmp_path):
    pf = _portfolio(tmp_path)
    res = pfr.paper_execute(pf, "BUY", "BTCUSDT", 100.0, 98.0, 103.0, {"BTCUSDT": 100.0}, str(tmp_path / "l.jsonl"))
    assert res["status"] == "FILLED"

    # Simulated restart: a brand-new process has no in-memory SL/TP map.
    restarted = _portfolio(tmp_path)
    pos = restarted.positions[res["pos_id"]]
    assert (pos["sl"], pos["tp"]) == (98.0, 103.0)
    assert pos["margin"] == pytest.approx(res["notional"])

    pfr.paper_exit_positions(restarted, {"BTCUSDT": 103.5}, _tp_candle())
    assert restarted.positions[res["pos_id"]]["status"] == "CLOSED"
    assert restarted.used_margin == pytest.approx(0.0, abs=1e-6)


def test_close_position_is_idempotent_and_releases_margin_once(tmp_path):
    pf = _portfolio(tmp_path)
    pf.allocate_margin(500.0, "open-1")
    pf.add_position("p1", "BTCUSDT", "LONG", 100.0, 5.0, metadata={"margin": 500.0})
    assert pf.close_position("p1", 110.0, exit_fee=1.0) is True
    cash_after_first_close = pf.cash
    assert pf.close_position("p1", 120.0, exit_fee=1.0) is False
    assert pf.close_position("missing", 120.0) is False

    assert pf.cash == pytest.approx(cash_after_first_close)
    assert pf.used_margin == pytest.approx(0.0)
    rows = _ledger_rows(pf)
    assert len(rows) == 1 and rows[0]["exit_price"] == 110.0
    assert rows[0]["margin_released"] == pytest.approx(500.0)


def test_close_position_legacy_positions_keep_explicit_margin_contract(tmp_path):
    pf = _portfolio(tmp_path)
    pf.allocate_margin(200.0, "open-legacy")
    pf.add_position("legacy", "BTCUSDT", "LONG", 100.0, 2.0)  # no recorded margin
    assert pf.close_position("legacy", 101.0) is True
    assert pf.used_margin == pytest.approx(200.0)  # caller still owns release
    pf.release_margin(200.0, "close-legacy")
    assert pf.used_margin == pytest.approx(0.0)


def test_kill_switch_flatten_releases_recorded_margin(tmp_path, monkeypatch):
    monkeypatch.setattr(kill_switch, "KILL_SWITCH_LOCK_FILE", str(tmp_path / "kill.lock"))
    pf = _portfolio(tmp_path)
    for i in range(3):
        # 10-point stop -> 1% risk sizes each position at ~10% of capital.
        res = pfr.paper_execute(pf, "BUY", "BTCUSDT", 100.0, 90.0, 110.0, {"BTCUSDT": 100.0}, str(tmp_path / "l.jsonl"))
        assert res["status"] == "FILLED", res
    assert pf.used_margin > 0

    summary = kill_switch.trigger_kill_switch("test flatten", portfolio=pf, current_market_prices={"BTCUSDT": 99.0})
    assert summary["positions_closed"] == 3
    assert pf.used_margin == pytest.approx(0.0, abs=1e-6)
    assert pf.cash + pf.used_margin == pytest.approx(pf.starting_capital + pf.realized_pnl)


def test_reconcile_margin_releases_orphans_without_changing_equity(tmp_path):
    pf = _portfolio(tmp_path)
    pf.allocate_margin(3000.0, "leaked-1")  # margin whose position was closed without release
    pf.allocate_margin(1000.0, "open-2")
    pf.add_position("still-open", "BTCUSDT", "LONG", 100.0, 10.0, metadata={"margin": 1000.0})
    equity_before = pf.get_equity({"BTCUSDT": 100.0})

    report = pf.reconcile_margin()
    assert report["orphaned_margin"] == pytest.approx(3000.0)
    assert report["repaired"] is False and pf.used_margin == pytest.approx(4000.0)

    report = pf.reconcile_margin(apply=True)
    assert report["repaired"] is True
    assert pf.used_margin == pytest.approx(1000.0)
    assert pf.get_equity({"BTCUSDT": 100.0}) == pytest.approx(equity_before)
    assert _portfolio(tmp_path).used_margin == pytest.approx(1000.0)  # persisted

    # Under-allocation is reported, never papered over with invented capital.
    pf.add_position("unfunded", "ETHUSDT", "LONG", 10.0, 50.0, metadata={"margin": 500.0})
    report = pf.reconcile_margin(apply=True)
    assert report["repaired"] is False
    assert report["under_allocated_margin"] == pytest.approx(500.0)
    assert pf.used_margin == pytest.approx(1000.0)


def _ab_engine(tmp_path):
    cfg = get_default_ab_config()
    for field in dataclasses.fields(cfg):
        if field.name.startswith(("state_file", "ledger_file", "equity_file")):
            setattr(cfg, field.name, str(tmp_path / f"{field.name}.json"))
    return abr.PaperABEngine(cfg), cfg


def test_ab_engine_books_fees_and_pnl_and_is_not_capped_by_closed_trades(tmp_path):
    engine, cfg = _ab_engine(tmp_path)
    pf = engine.portfolio_control
    for i in range(cfg.max_simultaneous_positions * 3):
        pos_id = engine.process_order(pf, "BTCUSDT", "BUY", 100.0, 95.0, 105.0)
        assert pos_id is not None, f"order {i} refused although no position is open"
        assert _portfolio(tmp_path, os.path.basename(cfg.state_file_control)).positions[pos_id]["status"] == "OPEN"
        engine.manage_positions_for_portfolio(pf, {"BTCUSDT": 106.0}, {})

    closed = [p for p in pf.positions.values() if p["status"] == "CLOSED"]
    assert len(closed) == cfg.max_simultaneous_positions * 3
    expected = sum(p["net_pnl"] - p["entry_fee"] - p["spread_cost"] for p in closed)
    assert expected > 0
    assert pf.realized_pnl == pytest.approx(expected)
    assert pf.used_margin == pytest.approx(0.0, abs=1e-6)
    assert pf.get_equity({"BTCUSDT": 106.0}) == pytest.approx(pf.starting_capital + expected)

    persisted = _portfolio(tmp_path, os.path.basename(cfg.state_file_control))
    assert all(p["status"] == "CLOSED" for p in persisted.positions.values())
    assert persisted.realized_pnl == pytest.approx(expected)


def test_ab_engine_losses_reach_equity_so_drawdown_guard_can_trip(tmp_path):
    engine, _cfg = _ab_engine(tmp_path)
    pf = engine.portfolio_control
    for _ in range(3):
        assert engine.process_order(pf, "BTCUSDT", "BUY", 100.0, 95.0, 105.0) is not None
    engine.manage_positions_for_portfolio(pf, {"BTCUSDT": 94.0}, {})
    assert pf.realized_pnl < 0
    assert pf.get_equity({"BTCUSDT": 94.0}) < pf.starting_capital


def test_long_simulation_harness_checks_accounting_and_stays_out_of_cwd(tmp_path, monkeypatch):
    from scripts import run_long_simulation as longsim

    workdir = tmp_path / "cwd"
    workdir.mkdir()
    monkeypatch.chdir(workdir)
    summary = longsim.run_simulation(events=3000, state_dir=str(tmp_path / "state"))
    assert all(summary["checks"].values()), summary["checks"]
    assert (summary["trades_opened"], summary["trades_closed"]) == (3, 3)
    assert math.isclose(summary["final_equity"], 10000.0 + summary["realized_pnl"], abs_tol=1e-6)
    # Only the process-wide logger may touch the CWD; no simulation state or ledger.
    assert [p.name for p in workdir.iterdir() if not p.name.endswith(".log")] == []
