"""Durable SQLite idempotency ledger for normalized Stratex order intents."""
from __future__ import annotations

import json
import os
import sqlite3
import time
from pathlib import Path
from typing import Any


class IntentConflictError(RuntimeError):
    """The same client-order ID was reused for a different intent."""


class IntentInFlightError(RuntimeError):
    """A prior submission is unresolved; resubmission would risk a duplicate fill."""


class OrderIntentStore:
    TERMINAL = {"ACCEPTED", "PARTIALLY_FILLED", "FILLED", "CANCELLED", "REJECTED", "UNKNOWN"}

    def __init__(self, path: str | None = None):
        self.path = path or os.getenv("STRATEX_ORDER_DB", "state/stratex_orders.sqlite3")
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self):
        conn = sqlite3.connect(self.path, timeout=10.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout=10000")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def _initialize(self) -> None:
        with self._connect() as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("""
                CREATE TABLE IF NOT EXISTS order_intents (
                    client_order_id TEXT PRIMARY KEY,
                    payload_hash TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    status TEXT NOT NULL,
                    result_json TEXT,
                    error_text TEXT,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_order_intents_status ON order_intents(status)")

    def reserve(self, client_order_id: str, payload_hash: str, payload: dict[str, Any]) -> dict[str, Any]:
        """Atomically reserve an ID. Returns whether it is new or an existing intent."""
        now = time.time()
        payload_json = json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False)
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT * FROM order_intents WHERE client_order_id = ?", (client_order_id,)
            ).fetchone()
            if row is None:
                conn.execute(
                    "INSERT INTO order_intents "
                    "(client_order_id,payload_hash,payload_json,status,created_at,updated_at) "
                    "VALUES (?,?,?,?,?,?)",
                    (client_order_id, payload_hash, payload_json, "RESERVED", now, now),
                )
                return {"kind": "new", "status": "RESERVED"}
            if row["payload_hash"] != payload_hash:
                raise IntentConflictError("client_order_id already belongs to a different trade intent")
            if row["status"] in {"RESERVED", "SUBMITTING", "UNKNOWN"}:
                raise IntentInFlightError(
                    f"Intent {client_order_id} has unresolved status {row['status']}; reconcile before retrying"
                )
            return {
                "kind": "duplicate",
                "status": row["status"],
                "result": json.loads(row["result_json"]) if row["result_json"] else None,
                "error": row["error_text"],
            }

    def transition(self, client_order_id: str, status: str, *,
                   result: dict[str, Any] | None = None, error: str | None = None) -> None:
        allowed = {"RESERVED", "SUBMITTING", *self.TERMINAL}
        if status not in allowed:
            raise ValueError(f"Unsupported order state: {status}")
        now = time.time()
        result_json = json.dumps(result, sort_keys=True, separators=(",", ":"), allow_nan=False) if result is not None else None
        with self._connect() as conn:
            cur = conn.execute(
                "UPDATE order_intents SET status=?, result_json=COALESCE(?, result_json), "
                "error_text=?, updated_at=? WHERE client_order_id=?",
                (status, result_json, error, now, client_order_id),
            )
            if cur.rowcount != 1:
                raise KeyError(f"Unknown order intent: {client_order_id}")

    def get(self, client_order_id: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM order_intents WHERE client_order_id=?", (client_order_id,)).fetchone()
        if row is None:
            return None
        item = dict(row)
        item["payload"] = json.loads(item.pop("payload_json"))
        item["result"] = json.loads(item.pop("result_json")) if item.get("result_json") else None
        item.pop("payload_hash", None)
        return item

    def list_unresolved(self, limit: int = 100) -> list[dict[str, Any]]:
        limit = min(1000, max(1, int(limit)))
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT client_order_id,status,created_at,updated_at,error_text "
                "FROM order_intents WHERE status IN ('RESERVED','SUBMITTING','UNKNOWN') "
                "ORDER BY created_at ASC LIMIT ?", (limit,)
            ).fetchall()
        return [dict(row) for row in rows]
