"""
ledger/live_ledger.py — Completely Isolated Live Capital Accounting & Reconciliation Engine.

Manages:
1. `live_trade_ledger.jsonl`: Every live trade execution with full audit trail.
2. `live_equity_curve.jsonl`: Live equity and available margin snapshots.
3. `live_balance_events.jsonl`: Deposits, withdrawals, and balance settlements.
4. `live_risk_events.jsonl`: Every risk check, trigger, and defensive action.
5. Automated Exchange Balance Reconciliation: Compares exchange balances against local state (Alert on mismatch > 0.5%).
"""

import datetime
import json
from typing import Any

from logger import get_logger

logger = get_logger("live_ledger")

LIVE_TRADE_LEDGER_FILE = "live_trade_ledger.jsonl"
LIVE_EQUITY_CURVE_FILE = "live_equity_curve.jsonl"
LIVE_BALANCE_EVENTS_FILE = "live_balance_events.jsonl"
LIVE_RISK_EVENTS_FILE = "live_risk_events.jsonl"


class LiveLedgerManager:
    """
    Handles append-only persistent storage and reconciliation for live execution.
    """

    def __init__(
        self,
        trade_file: str = LIVE_TRADE_LEDGER_FILE,
        equity_file: str = LIVE_EQUITY_CURVE_FILE,
        balance_file: str = LIVE_BALANCE_EVENTS_FILE,
        risk_file: str = LIVE_RISK_EVENTS_FILE
    ):
        self.trade_file = trade_file
        self.equity_file = equity_file
        self.balance_file = balance_file
        self.risk_file = risk_file

    def record_live_trade(self, trade_data: dict[str, Any]) -> None:
        """Appends closed live trade record."""
        trade_data["recorded_at"] = datetime.datetime.utcnow().isoformat() + "Z"
        with open(self.trade_file, "a", encoding="utf-8") as f:
            f.write(json.dumps(trade_data) + "\n")
        logger.info(f"[LIVE_LEDGER] Recorded live trade {trade_data.get('trade_id', 'UNKNOWN')} PnL: ${trade_data.get('net_pnl', 0.0):.2f}")

    def record_live_equity_snapshot(self, equity: float, cash: float, used_margin: float = 0.0) -> None:
        """Appends live equity snapshot."""
        entry = {
            "timestamp": datetime.datetime.utcnow().isoformat() + "Z",
            "equity": round(equity, 2),
            "cash": round(cash, 2),
            "used_margin": round(used_margin, 2)
        }
        with open(self.equity_file, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")

    def record_live_risk_event(self, event_type: str, details: dict[str, Any]) -> None:
        """Appends risk event record."""
        entry = {
            "timestamp": datetime.datetime.utcnow().isoformat() + "Z",
            "event_type": event_type,
            "details": details
        }
        with open(self.risk_file, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")

    def reconcile_exchange_balance(
        self,
        exchange_reported_balance: float,
        local_calculated_balance: float,
        max_mismatch_pct: float = 0.005  # 0.5% tolerance
    ) -> tuple[bool, float, str]:
        """
        Compares Binance reported balance with internal ledger balance.
        Returns: (is_reconciled, discrepancy_pct, summary_message)
        """
        if local_calculated_balance <= 0:
            return True, 0.0, "Zero baseline balance"

        diff = abs(exchange_reported_balance - local_calculated_balance)
        discrepancy_pct = (diff / local_calculated_balance) * 100.0

        if discrepancy_pct > (max_mismatch_pct * 100.0):
            msg = (
                f"RECONCILIATION_ALERT: Exchange balance (${exchange_reported_balance:.2f}) differs from local "
                f"(${local_calculated_balance:.2f}) by {discrepancy_pct:.2f}% (Limit {max_mismatch_pct*100}%)."
            )
            logger.critical(f"[LIVE_RECONCILIATION] 🚨 {msg}")
            self.record_live_risk_event("BALANCE_DISCREPANCY_ALERT", {
                "exchange_balance": exchange_reported_balance,
                "local_balance": local_calculated_balance,
                "discrepancy_pct": discrepancy_pct
            })
            return False, discrepancy_pct, msg

        return True, discrepancy_pct, f"Reconciled successfully (Discrepancy: {discrepancy_pct:.3f}%)"
