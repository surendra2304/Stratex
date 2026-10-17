"""Persistent MT5 bridge idempotency ledger.

An order identifier is reserved before order_send. An ambiguous response is never
silently retried: operators must reconcile the MT5 terminal state first.
"""
from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
import time
from pathlib import Path
from typing import Any


class DuplicateOrderIdConflict(RuntimeError):
    pass


class UnresolvedBridgeOrder(RuntimeError):
    pass


class BridgeOrderStore:
    TERMINAL_STATES = {"ACCEPTED", "PARTIALLY_FILLED", "FILLED", "REJECTED", "CANCELLED"}

    def __init__(self, path: str):
        self.path = path
        if path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        with self._managed_connection() as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("""
                CREATE TABLE IF NOT EXISTS bridge_orders (
                    client_order_id TEXT PRIMARY KEY,
                    request_hash TEXT NOT NULL,
                    request_json TEXT NOT NULL,
                    status TEXT NOT NULL,
                    response_json TEXT,
                    error_text TEXT,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL
                )
            """)

    def _connect(self):
        conn = sqlite3.connect(self.path, timeout=10.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout=10000")
        return conn

    @contextmanager
    def _managed_connection(self):
        """Close SQLite connections reliably; Connection.__exit__ only commits/rolls back."""
        conn = self._connect()
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def lookup(self, client_order_id: str, request_hash: str) -> dict[str, Any] | None:
        """Return a cached terminal result before dynamic broker checks; do not reserve new IDs."""
        with self._managed_connection() as conn:
            row = conn.execute(
                "SELECT * FROM bridge_orders WHERE client_order_id=?", (client_order_id,)
            ).fetchone()
        if row is None:
            return None
        if row["request_hash"] != request_hash:
            raise DuplicateOrderIdConflict("client_order_id was already used for a different payload")
        if row["status"] not in self.TERMINAL_STATES:
            raise UnresolvedBridgeOrder(
                f"order {client_order_id} is {row['status']}; do not resubmit until reconciled"
            )
        return {
            "kind": "duplicate",
            "status": row["status"],
            "response": json.loads(row["response_json"]) if row["response_json"] else {},
        }

    def reserve(self, client_order_id: str, request_hash: str, request: dict[str, Any]) -> dict[str, Any]:
        now = time.time()
        payload = json.dumps(request, sort_keys=True, separators=(",", ":"), allow_nan=False)
        with self._managed_connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT * FROM bridge_orders WHERE client_order_id=?", (client_order_id,)
            ).fetchone()
            if row:
                if row["request_hash"] != request_hash:
                    raise DuplicateOrderIdConflict("client_order_id was already used for a different payload")
                if row["status"] not in self.TERMINAL_STATES:
                    raise UnresolvedBridgeOrder(
                        f"order {client_order_id} is {row['status']}; do not resubmit until reconciled"
                    )
                return {
                    "kind": "duplicate",
                    "status": row["status"],
                    "response": json.loads(row["response_json"]) if row["response_json"] else {},
                }
            conn.execute(
                "INSERT INTO bridge_orders "
                "(client_order_id,request_hash,request_json,status,created_at,updated_at) "
                "VALUES (?,?,?,?,?,?)",
                (client_order_id, request_hash, payload, "SUBMITTING", now, now),
            )
            return {"kind": "new", "status": "SUBMITTING"}

    def finish(self, client_order_id: str, status: str, response: dict[str, Any] | None = None,
               error: str | None = None) -> None:
        if status not in {"ACCEPTED", "FILLED", "REJECTED", "CANCELLED", "UNKNOWN"}:
            raise ValueError(f"Invalid MT5 bridge order state: {status}")
        response_json = json.dumps(response, sort_keys=True, separators=(",", ":"), allow_nan=False) if response is not None else None
        with self._managed_connection() as conn:
            cur = conn.execute(
                "UPDATE bridge_orders SET status=?, response_json=COALESCE(?,response_json), "
                "error_text=?, updated_at=? WHERE client_order_id=?",
                (status, response_json, error, time.time(), client_order_id),
            )
            if cur.rowcount != 1:
                raise KeyError(f"Unknown MT5 client order id: {client_order_id}")

    def get(self, client_order_id: str) -> dict[str, Any] | None:
        with self._managed_connection() as conn:
            row = conn.execute("SELECT * FROM bridge_orders WHERE client_order_id=?", (client_order_id,)).fetchone()
        if row is None:
            return None
        result = dict(row)
        result["response"] = json.loads(result.pop("response_json")) if result.get("response_json") else None
        result.pop("request_hash", None)
        result.pop("request_json", None)
        return result
