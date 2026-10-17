"""Durable SQLite idempotency ledger for normalized Stratex order intents."""
from __future__ import annotations

import json
import os
import sqlite3
from contextlib import contextmanager
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

    def _initialize(self) -> None:
        with self._managed_connection() as conn:
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
            conn.execute("""
                CREATE TABLE IF NOT EXISTS order_audit_events (
                    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    client_order_id TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    previous_status TEXT,
                    new_status TEXT,
                    details_json TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    FOREIGN KEY(client_order_id) REFERENCES order_intents(client_order_id)
                )
            """)
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_order_audit_client_time "
                "ON order_audit_events(client_order_id, created_at)"
            )
            conn.execute("""
                CREATE TRIGGER IF NOT EXISTS order_audit_no_update
                BEFORE UPDATE ON order_audit_events
                BEGIN SELECT RAISE(ABORT, 'audit events are append-only'); END
            """)
            conn.execute("""
                CREATE TRIGGER IF NOT EXISTS order_audit_no_delete
                BEFORE DELETE ON order_audit_events
                BEGIN SELECT RAISE(ABORT, 'audit events are append-only'); END
            """)

    @staticmethod
    def _append_audit_event(
        conn,
        *,
        client_order_id: str,
        event_type: str,
        previous_status: str | None,
        new_status: str | None,
        details: dict[str, Any],
        timestamp: float,
    ) -> None:
        details_json = json.dumps(details, sort_keys=True, separators=(",", ":"), allow_nan=False)
        conn.execute(
            "INSERT INTO order_audit_events "
            "(client_order_id,event_type,previous_status,new_status,details_json,created_at) "
            "VALUES (?,?,?,?,?,?)",
            (client_order_id, event_type, previous_status, new_status, details_json, timestamp),
        )

    @staticmethod
    def _result_summary(result: dict[str, Any] | None) -> dict[str, Any] | None:
        if not isinstance(result, dict):
            return None
        allowed = (
            "status", "order_id", "deal", "avg_price", "price", "quantity",
            "volume_filled", "venue", "environment", "protection_verified",
            "stop_algo_id", "take_profit_algo_id", "reason",
        )
        summary = {key: result[key] for key in allowed if key in result}
        return summary

    def reserve(self, client_order_id: str, payload_hash: str, payload: dict[str, Any]) -> dict[str, Any]:
        """Atomically reserve an ID. Returns whether it is new or an existing intent."""
        now = time.time()
        payload_json = json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False)
        with self._managed_connection() as conn:
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
                safe_payload = {
                    key: payload.get(key)
                    for key in ("venue", "symbol", "side", "environment", "quantity", "strategy")
                    if key in payload
                }
                self._append_audit_event(
                    conn, client_order_id=client_order_id, event_type="INTENT_RESERVED",
                    previous_status=None, new_status="RESERVED",
                    details={"payload_hash": payload_hash, **safe_payload}, timestamp=now,
                )
                return {"kind": "new", "status": "RESERVED"}
            if row["payload_hash"] != payload_hash:
                raise IntentConflictError("client_order_id already belongs to a different trade intent")
            if row["status"] in {"RESERVED", "SUBMITTING", "UNKNOWN"}:
                raise IntentInFlightError(
                    f"Intent {client_order_id} has unresolved status {row['status']}; reconcile before retrying"
                )
            self._append_audit_event(
                conn, client_order_id=client_order_id, event_type="DUPLICATE_REQUEST",
                previous_status=row["status"], new_status=row["status"],
                details={"payload_hash": payload_hash}, timestamp=now,
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
        with self._managed_connection() as conn:
            row = conn.execute(
                "SELECT status FROM order_intents WHERE client_order_id=?", (client_order_id,)
            ).fetchone()
            if row is None:
                raise KeyError(f"Unknown order intent: {client_order_id}")
            previous_status = str(row["status"]).upper()
            progressions = {
                "RESERVED": {"RESERVED", "SUBMITTING", "ACCEPTED", "PARTIALLY_FILLED", "FILLED", "CANCELLED", "REJECTED", "UNKNOWN"},
                "SUBMITTING": {"SUBMITTING", "ACCEPTED", "PARTIALLY_FILLED", "FILLED", "CANCELLED", "REJECTED", "UNKNOWN"},
                "UNKNOWN": {"UNKNOWN", "ACCEPTED", "PARTIALLY_FILLED", "FILLED", "CANCELLED", "REJECTED"},
                "ACCEPTED": {"ACCEPTED", "PARTIALLY_FILLED", "FILLED", "CANCELLED", "REJECTED"},
                "PARTIALLY_FILLED": {"PARTIALLY_FILLED", "FILLED", "CANCELLED"},
                "FILLED": {"FILLED"},
                "CANCELLED": {"CANCELLED"},
                "REJECTED": {"REJECTED"},
            }
            if status not in progressions.get(previous_status, set()):
                raise ValueError(f"Invalid order lifecycle transition: {previous_status} -> {status}")
            conn.execute(
                "UPDATE order_intents SET status=?, result_json=COALESCE(?, result_json), "
                "error_text=?, updated_at=? WHERE client_order_id=?",
                (status, result_json, error, now, client_order_id),
            )
            self._append_audit_event(
                conn, client_order_id=client_order_id, event_type="ORDER_STATE_TRANSITION",
                previous_status=previous_status, new_status=status,
                details={
                    "result": self._result_summary(result),
                    "error": str(error)[:300] if error else None,
                },
                timestamp=now,
            )

    def get(self, client_order_id: str) -> dict[str, Any] | None:
        with self._managed_connection() as conn:
            row = conn.execute("SELECT * FROM order_intents WHERE client_order_id=?", (client_order_id,)).fetchone()
        if row is None:
            return None
        item = dict(row)
        item["payload"] = json.loads(item.pop("payload_json"))
        item["result"] = json.loads(item.pop("result_json")) if item.get("result_json") else None
        item.pop("payload_hash", None)
        return item

    def list_reconcilable(self, limit: int = 100) -> list[dict[str, Any]]:
        """List non-final or exchange-pending intents for periodic venue reconciliation."""
        limit = min(1000, max(1, int(limit)))
        with self._managed_connection() as conn:
            rows = conn.execute(
                "SELECT client_order_id,status,created_at,updated_at,error_text "
                "FROM order_intents "
                "WHERE status IN ('RESERVED','SUBMITTING','UNKNOWN','ACCEPTED','PARTIALLY_FILLED') "
                "ORDER BY created_at ASC LIMIT ?",
                (limit,),
            ).fetchall()
        return [dict(row) for row in rows]

    def list_audit_events(
        self,
        *,
        client_order_id: str | None = None,
        limit: int = 1000,
    ) -> list[dict[str, Any]]:
        """Read append-only audit events in chronological order."""
        bounded_limit = min(10000, max(1, int(limit)))
        with self._managed_connection() as conn:
            if client_order_id:
                rows = conn.execute(
                    "SELECT * FROM order_audit_events WHERE client_order_id=? "
                    "ORDER BY event_id ASC LIMIT ?",
                    (client_order_id, bounded_limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM order_audit_events ORDER BY event_id ASC LIMIT ?",
                    (bounded_limit,),
                ).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item["details"] = json.loads(item.pop("details_json"))
            result.append(item)
        return result

    def list_unresolved(self, limit: int = 100) -> list[dict[str, Any]]:
        limit = min(1000, max(1, int(limit)))
        with self._managed_connection() as conn:
            rows = conn.execute(
                "SELECT client_order_id,status,created_at,updated_at,error_text "
                "FROM order_intents WHERE status IN ('RESERVED','SUBMITTING','UNKNOWN') "
                "ORDER BY created_at ASC LIMIT ?", (limit,)
            ).fetchall()
        return [dict(row) for row in rows]
