from __future__ import annotations

import json
import os
import tempfile
import time
from collections.abc import Iterable
from dataclasses import dataclass
from decimal import Decimal
from typing import Any


@dataclass(frozen=True, slots=True)
class ReconciliationIssue:
    category: str
    key: str
    local: Any
    remote: Any
    severity: str = "ERROR"


@dataclass(frozen=True, slots=True)
class ReconciliationReport:
    ok: bool
    timestamp_ns: int
    issues: tuple[ReconciliationIssue, ...]
    local_order_count: int
    remote_order_count: int
    local_position_count: int
    remote_position_count: int


class AtomicJsonStore:
    """Crash-safe state persistence using write-to-temp + replace."""

    def __init__(self, path: str):
        self.path = path

    def read(self, default: Any) -> Any:
        if not os.path.exists(self.path):
            return default
        with open(self.path, "r", encoding="utf-8") as fh:
            return json.load(fh)

    def write(self, data: Any) -> None:
        parent = os.path.dirname(os.path.abspath(self.path))
        os.makedirs(parent, exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix=".stx-", suffix=".tmp", dir=parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(data, fh, sort_keys=True, separators=(",", ":"))
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp, self.path)
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)


class ExchangeReconciler:
    """Venue-authoritative reconciliation after restart and periodically at runtime."""

    def __init__(self, adapter, issue_tolerance: Decimal = Decimal("0.00000001")):
        self.adapter = adapter
        self.issue_tolerance = issue_tolerance

    def reconcile(self, local_orders: Iterable[dict], local_positions: Iterable[dict]) -> ReconciliationReport:
        local_orders = list(local_orders)
        local_positions = list(local_positions)
        remote_orders = self.adapter.fetch_open_orders()
        remote_positions = self.adapter.fetch_positions()
        issues: list[ReconciliationIssue] = []

        local_by_client = {str(x.get("clientOrderId")): x for x in local_orders if x.get("clientOrderId")}
        remote_by_client = {str(x.get("clientOrderId")): x for x in remote_orders if x.get("clientOrderId")}

        for cid, remote in remote_by_client.items():
            if cid not in local_by_client:
                issues.append(ReconciliationIssue("UNTRACKED_REMOTE_ORDER", cid, None, remote, "CRITICAL"))

        for cid, local in local_by_client.items():
            if cid not in remote_by_client:
                status = str(local.get("status", "")).upper()
                if status not in {"FILLED", "CANCELED", "CANCELLED", "EXPIRED", "REJECTED"}:
                    issues.append(ReconciliationIssue("MISSING_REMOTE_ORDER", cid, local, None, "CRITICAL"))

        def key_position(p: dict) -> tuple[str, str]:
            return str(p.get("symbol")), str(p.get("side", "LONG"))

        local_pos = {key_position(p): p for p in local_positions}
        remote_pos = {key_position(p): p for p in remote_positions}
        for key in sorted(set(local_pos) | set(remote_pos)):
            lp = local_pos.get(key)
            rp = remote_pos.get(key)
            if lp is None or rp is None:
                issues.append(ReconciliationIssue("POSITION_MISMATCH", str(key), lp, rp, "CRITICAL"))
                continue
            lq = Decimal(str(lp.get("quantity", "0")))
            rq = Decimal(str(rp.get("quantity", "0")))
            if abs(lq - rq) > self.issue_tolerance:
                issues.append(ReconciliationIssue("POSITION_QUANTITY_MISMATCH", str(key), lp, rp, "CRITICAL"))

        return ReconciliationReport(
            ok=not issues,
            timestamp_ns=time.time_ns(),
            issues=tuple(issues),
            local_order_count=len(local_orders),
            remote_order_count=len(remote_orders),
            local_position_count=len(local_positions),
            remote_position_count=len(remote_positions),
        )
