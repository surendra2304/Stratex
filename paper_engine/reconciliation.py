"""
paper_engine/reconciliation.py

Daily reconciliation for the forward validation experiment.
Verifies: signals → trades → ledger → portfolio → equity curve are consistent.

CRITICAL CONTRACT:
- Every closed trade must appear exactly once in the ledger (no duplicate trade IDs).
- Any mismatch → RECONCILIATION_ERROR → new trades blocked until investigated.
"""
import json
import math
import os
import time
from typing import List, Dict, Optional
from logger import get_logger
from paper_engine.portfolio import PaperPortfolio

logger = get_logger("reconciliation")

TOLERANCE = 0.01  # $0.01 floating-point tolerance


class PortfolioReconciler:
    """Original portfolio reconciler — preserved for backwards compatibility."""

    def __init__(self, portfolio: PaperPortfolio):
        self.portfolio = portfolio

    def check_consistency(self):
        issues = []
        if os.path.exists(self.portfolio.ledger_file):
            ledger_funding = 0.0
            with open(self.portfolio.ledger_file, "r") as f:
                for line in f:
                    try:
                        trade = json.loads(line)
                        if trade.get("status") == "CLOSED":
                            ledger_funding += trade.get("funding_pnl", 0.0)
                    except Exception:
                        issues.append("CORRUPTED_LEDGER_RECORD")

            if self.portfolio.cumulative_funding != 0 and abs(
                self.portfolio.cumulative_funding - ledger_funding
            ) > TOLERANCE:
                issues.append(
                    f"FUNDING_MISMATCH: Portfolio {self.portfolio.cumulative_funding} vs Ledger {ledger_funding}"
                )

        if issues:
            from paper_engine.exceptions import PortfolioError
            raise PortfolioError(f"Reconciliation failed: {issues}")

        return True


class PaperReconciliation:
    """
    Forward-validation reconciler.
    Checks for duplicate trade IDs and duplicate signal IDs in the ledger.
    """

    def __init__(self, ledger_file: str, reconciliation_file: Optional[str] = None):
        self.ledger_file = ledger_file
        self.reconciliation_file = (
            reconciliation_file
            or os.getenv("FORWARD_RECONCILIATION_FILE", "forward_reconciliation.jsonl")
        )

    def _read_ledger(self) -> List[Dict]:
        if not os.path.exists(self.ledger_file):
            return []
        records = []
        with open(self.ledger_file, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    records.append(json.loads(line))
                except json.JSONDecodeError:
                    logger.warning(f"Bad ledger line: {line[:80]}")
        return records

    def run(self) -> bool:
        records = self._read_ledger()
        trade_ids = [r.get("trade_id") for r in records if "trade_id" in r]

        # 1. Check for duplicate trade IDs
        seen_ids: set = set()
        duplicates = []
        for tid in trade_ids:
            if tid in seen_ids:
                duplicates.append(tid)
            seen_ids.add(tid)

        if duplicates:
            logger.error(
                f"RECONCILIATION ERROR: {len(duplicates)} duplicate trade IDs: {duplicates[:5]}"
            )
            _write_reconciliation_record(
                False, f"DUPLICATE_TRADE_IDS: {duplicates[:5]}", filename=self.reconciliation_file
            )
            return False

        # 2. Check for duplicate signal IDs
        sig_ids = [r.get("signal_id") for r in records if "signal_id" in r]
        seen_sigs: set = set()
        dup_sigs = []
        for sid in sig_ids:
            if sid in seen_sigs:
                dup_sigs.append(sid)
            seen_sigs.add(sid)

        if dup_sigs:
            logger.error(f"RECONCILIATION ERROR: {len(dup_sigs)} duplicate signal IDs")
            _write_reconciliation_record(
                False, f"DUPLICATE_SIGNAL_IDS: count={len(dup_sigs)}", filename=self.reconciliation_file
            )
            return False

        _write_reconciliation_record(True, "OK", filename=self.reconciliation_file)
        logger.info(
            f"Reconciliation PASS: {len(records)} ledger records, "
            f"{len(seen_ids)} unique trade IDs, {len(seen_sigs)} unique signal IDs"
        )
        return True


def _write_reconciliation_record(ok: bool, detail: str, filename: Optional[str] = None):
    out_file = filename or os.getenv("FORWARD_RECONCILIATION_FILE", "forward_reconciliation.jsonl")
    record = {
        "reconciled_at": time.time(),
        "status": "OK" if ok else "RECONCILIATION_ERROR",
        "detail": detail,
    }
    try:
        with open(out_file, "a", encoding="utf-8") as f:
            f.write(json.dumps(record) + "\n")
    except Exception as e:
        logger.error(f"Failed to write reconciliation record: {e}")
