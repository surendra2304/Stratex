"""
paper_engine/kill_switch.py

Emergency halt mechanism for paper trading sessions.

CRITICAL CONTRACT:
- Kill-switch NEVER closes positions at zero cost.
- Every forced exit applies the same realistic execution model as a normal market exit.
- Forced exits are recorded in the ledger with reason="KILL_SWITCH".
- The kill switch may stop new signals immediately.
- Existing positions are closed using realistic costs (taker fees + slippage + spread).
"""
import os
import json
import time
import uuid
from typing import Optional
from logger import get_logger
from research_phase9.cost_engine import CostEngine

logger = get_logger("kill_switch")

# Lock file written atomically when kill switch is triggered.
KILL_SWITCH_LOCK_FILE = "KILL_SWITCH_ACTIVE.lock"


def trigger_kill_switch(
    reason: str,
    portfolio=None,
    current_market_prices: Optional[dict] = None,
    cost_engine: Optional[CostEngine] = None,
) -> dict:
    """
    Triggers the emergency halt mechanism.

    Sequence:
    1. Write lock file — immediately blocks all new signal processing.
    2. Flatten all open positions with REALISTIC execution costs.
    3. Record every forced closure in the ledger with reason=KILL_SWITCH.
    4. Log a critical summary.
    5. Return a summary dict (does NOT call sys.exit — caller decides).

    Parameters
    ----------
    reason : Human-readable trigger reason.
    portfolio : PaperPortfolio instance. If None, only the lock file is written.
    current_market_prices : Dict[symbol -> price]. Must be provided if portfolio is given.
    cost_engine : CostEngine to apply. Defaults to Binance Taker config (worst-case realistic).

    Returns
    -------
    dict with: triggered_at, reason, positions_closed, total_exit_cost, total_exit_pnl
    """
    triggered_at = time.time()
    logger.critical(f"KILL SWITCH TRIGGERED — Reason: {reason}")

    # ── 1. Write atomic lock file ──────────────────────────────────────────
    lock_data = {
        "triggered_at": triggered_at,
        "reason": reason,
        "git_sha": _get_git_sha(),
    }
    try:
        tmp = KILL_SWITCH_LOCK_FILE + ".tmp"
        with open(tmp, "w") as f:
            json.dump(lock_data, f, indent=4)
        os.replace(tmp, KILL_SWITCH_LOCK_FILE)
        logger.info(f"Kill switch lock file written: {KILL_SWITCH_LOCK_FILE}")
    except Exception as e:
        logger.error(f"Failed to write kill switch lock file: {e}")

    # ── 2. Flatten positions with realistic costs ──────────────────────────
    summary = {
        "triggered_at": triggered_at,
        "reason": reason,
        "positions_closed": 0,
        "positions_skipped": 0,
        "total_exit_cost": 0.0,
        "total_exit_pnl": 0.0,
        "errors": [],
    }

    if portfolio is None:
        logger.warning("Kill switch: no portfolio provided — positions NOT flattened.")
        return summary

    cost = cost_engine or CostEngine.get_binance_taker_config()
    prices = current_market_prices or {}

    for pos_id, pos in list(portfolio.positions.items()):
        if pos["status"] != "OPEN":
            continue

        symbol = pos["symbol"]
        direction = pos["direction"]
        qty = pos["quantity"]
        entry_price = pos["entry_price"]

        # Get exit price from market data; fall back to entry price with a warning
        if symbol in prices:
            raw_exit_price = prices[symbol]
        else:
            raw_exit_price = entry_price
            logger.warning(
                f"Kill switch: no market price for {symbol} — using entry price {entry_price}. "
                "PnL may be inaccurate."
            )
            summary["errors"].append(f"NO_PRICE_FOR_{symbol}")

        notional = raw_exit_price * qty

        # Apply exit slippage (adverse — always costs extra on forced exits)
        if direction in ("LONG", "BUY"):
            # Selling: slippage pushes price DOWN
            eff_exit_price = raw_exit_price * (1.0 - cost.exit_slip)
        else:
            # Covering short: slippage pushes price UP
            eff_exit_price = raw_exit_price * (1.0 + cost.exit_slip)

        # Costs
        exit_fee = notional * cost.exit_fee
        spread_cost = notional * cost.spread

        # Gross PnL
        if direction in ("LONG", "BUY"):
            gross_pnl = (eff_exit_price - entry_price) * qty
        else:
            gross_pnl = (entry_price - eff_exit_price) * qty

        net_pnl = gross_pnl - exit_fee - spread_cost
        total_exit_cost = exit_fee + spread_cost

        try:
            # Use a unique event ID so this exit is idempotent
            exit_event_id = f"ks_{pos_id}_{str(uuid.uuid4())[:8]}"
            portfolio.close_position(
                pos_id,
                eff_exit_price,
                exit_fee=exit_fee,
                funding_pnl=0.0,
            )
            # Record PnL in portfolio cash
            portfolio.add_realized_pnl(net_pnl, exit_event_id)

            # Append kill-switch metadata to the ledger record
            _append_kill_switch_metadata(portfolio.ledger_file, pos_id, reason)

            summary["positions_closed"] += 1
            summary["total_exit_cost"] += total_exit_cost
            summary["total_exit_pnl"] += net_pnl

            logger.info(
                f"Kill switch closed {pos_id} ({symbol} {direction}): "
                f"exit={eff_exit_price:.4f} net_pnl={net_pnl:.4f} cost={total_exit_cost:.4f}"
            )

        except Exception as e:
            summary["positions_skipped"] += 1
            summary["errors"].append(f"CLOSE_FAILED_{pos_id}: {e}")
            logger.error(f"Kill switch failed to close {pos_id}: {e}")

    logger.critical(
        f"Kill switch complete — closed {summary['positions_closed']} positions, "
        f"total_exit_cost={summary['total_exit_cost']:.4f}, "
        f"total_exit_pnl={summary['total_exit_pnl']:.4f}"
    )
    return summary


def _append_kill_switch_metadata(ledger_file: str, pos_id: str, reason: str):
    """
    Marks the most recent ledger entry for pos_id with kill_switch reason.
    Does not rewrite existing entries — appends a metadata annotation record.
    """
    try:
        annotation = {
            "type": "KILL_SWITCH_ANNOTATION",
            "trade_id": pos_id,
            "reason": reason,
            "annotated_at": time.time(),
        }
        with open(ledger_file, "a", encoding="utf-8") as f:
            f.write(json.dumps(annotation) + "\n")
    except Exception as e:
        logger.error(f"Failed to annotate ledger for kill switch: {e}")


def _get_git_sha() -> str:
    """Returns the current Git HEAD SHA for experiment traceability."""
    try:
        import subprocess
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True, text=True, timeout=5
        )
        return result.stdout.strip() if result.returncode == 0 else "UNKNOWN"
    except Exception:
        return "UNKNOWN"


def is_kill_switch_active() -> bool:
    """Returns True if the kill switch lock file exists."""
    return os.path.exists(KILL_SWITCH_LOCK_FILE)


def reset_kill_switch():
    """
    Removes the kill switch lock file.
    ONLY call this after manual human review and explicit operator decision.
    """
    if os.path.exists(KILL_SWITCH_LOCK_FILE):
        os.remove(KILL_SWITCH_LOCK_FILE)
        logger.warning("Kill switch RESET by operator. System can now accept new signals.")
