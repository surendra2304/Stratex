"""Long-running paper-engine chaos simulation (100,000 synthetic minute ticks).

Exercises the data monitor (1% duplicate ticks, periodic 2-minute gaps that also
produce out-of-order ticks) and the paper portfolio lifecycle (one round trip
every 1,000 ticks) and then *checks* the accounting instead of just printing it:

* every opened position is closed, no margin is left locked;
* booked realized PnL equals the sum of per-trade net PnL in the trade ledger;
* cash + used margin == starting capital + realized PnL (equity identity).

All state files go to a private temporary directory (or ``--state-dir``) so a
stress run can never append synthetic trades to an operator's real
``paper_trade_ledger.jsonl`` in the working directory.

Exit code is 0 only when every invariant holds.
"""

import argparse
import json
import math
import os
import sys
import tempfile
import time

# Add root directory to python path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from paper_engine.alerts import AlertManager
from paper_engine.data_monitor import DataMonitor
from paper_engine.heartbeat import HeartbeatState
from paper_engine.portfolio import PaperPortfolio
from paper_engine.session import SessionState

MARGIN_PER_TRADE = 100.0
QTY = 0.002
EXIT_FEE = 0.5


def run_simulation(events: int = 100000, state_dir: str | None = None) -> dict:
    state_dir = state_dir or tempfile.mkdtemp(prefix="stratex_longsim_")
    os.makedirs(state_dir, exist_ok=True)
    print(f"Starting {events:,} Event Simulation... (state dir: {state_dir})")

    session = SessionState(filename=os.path.join(state_dir, "sim_session.json"))
    session.start_session({"mode": "SIMULATION"})

    hb = HeartbeatState(filename=os.path.join(state_dir, "sim_heartbeat.json"))
    dm = DataMonitor(hb)
    AlertManager(filename=os.path.join(state_dir, "sim_alerts.json"))
    port = PaperPortfolio(filename=os.path.join(state_dir, "sim_portfolio.json"))
    starting_capital = port.starting_capital

    start_t = time.time()
    wall_start = time.perf_counter()
    opened = closed = 0

    for i in range(events):
        t = start_t + i * 60
        price = 50000 + (i % 100) * 10

        # 1. Feed Data
        dm.process_tick("BTCUSDT", price, t)

        # 2. Chaos Injection: 1% Duplicate Data
        if i % 100 == 0:
            dm.process_tick("BTCUSDT", price, t)

        # 3. Chaos Injection: periodic 2-minute jump (next regular tick arrives out of order)
        if i % 150 == 0:
            t += 120
            dm.process_tick("BTCUSDT", price, t)

        # 4. Strategy / Portfolio interaction (one round trip every 1,000 ticks)
        if i % 1000 == 0:
            pos_id = f"pos_{i}"
            port.allocate_margin(MARGIN_PER_TRADE, f"event_{i}")
            # Margin recorded on the position is released by close_position().
            port.add_position(pos_id, "BTCUSDT", "BUY", price, QTY, metadata={"margin": MARGIN_PER_TRADE})
            opened += 1

        if i % 1000 == 500:
            pos_id = f"pos_{i - 500}"
            exit_price = price + 50
            entry_price = port.positions[pos_id]["entry_price"]
            if port.close_position(pos_id, exit_price, EXIT_FEE, t, 0.0):
                net_pnl = (exit_price - entry_price) * QTY - EXIT_FEE
                port.add_realized_pnl(net_pnl, f"event_exit_pnl_{i}")
                closed += 1

        if i % 5000 == 0:
            eq = port.get_equity({"BTCUSDT": price})
            assert eq > 0, "Equity corrupted!"

    session.stop_session()
    wall = time.perf_counter() - wall_start

    with open(port.ledger_file, encoding="utf-8") as handle:
        ledger_rows = [json.loads(line) for line in handle if line.strip()]
    ledger_net = sum(row["net_pnl"] for row in ledger_rows)
    open_positions = sum(1 for p in port.positions.values() if p["status"] == "OPEN")
    final_equity = port.get_equity({"BTCUSDT": 50000})

    checks = {
        "all_positions_closed": open_positions == 0 and opened == closed,
        "no_locked_margin": abs(port.used_margin) < 1e-9,
        "ledger_rows_match_closed_trades": len(ledger_rows) == closed,
        "realized_pnl_matches_ledger": math.isclose(port.realized_pnl, ledger_net, abs_tol=1e-9),
        "equity_identity": math.isclose(
            port.cash + port.used_margin, starting_capital + port.realized_pnl, abs_tol=1e-6
        ),
    }
    state = dm.symbols["BTCUSDT"]
    summary = {
        "events": events,
        "duplicates": state["duplicates"],
        "gaps": state["gaps"],
        "out_of_order": state["out_of_order"],
        "trades_opened": opened,
        "trades_closed": closed,
        "realized_pnl": round(port.realized_pnl, 6),
        "final_equity": round(final_equity, 6),
        "wall_seconds": round(wall, 2),
        "checks": checks,
        "state_dir": state_dir,
    }

    print("Simulation Complete!")
    print(f"Events Processed: {events}")
    print(f"Data Duplicates Handled: {state['duplicates']}")
    print(f"Data Gaps Detected: {state['gaps']}")
    print(f"Out-of-order Ticks Detected: {state['out_of_order']}")
    print(f"Trades Opened/Closed: {opened}/{closed}")
    print(f"Realized PnL: {port.realized_pnl:.4f}")
    print(f"Final Equity: {final_equity:.4f}")
    for name, ok in checks.items():
        print(f"CHECK {name}: {'PASS' if ok else 'FAIL'}")
    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--events", type=int, default=100000)
    parser.add_argument("--state-dir", default=None, help="directory for simulation state (default: fresh temp dir)")
    args = parser.parse_args(argv)
    summary = run_simulation(args.events, args.state_dir)
    return 0 if all(summary["checks"].values()) else 1


if __name__ == "__main__":
    sys.exit(main())
