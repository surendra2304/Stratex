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
        # Prefer the agent's authoritative PostgreSQL database when configured.
        # Explicit paths remain useful for isolated SQLite unit tests and local dev.
        configured_dsn = (os.getenv("STRATEX_DATABASE_URL") or "").strip()
        postgres_prefixes = ("postgresql://", "postgres://", "postgresql+psycopg://")
        if path is not None:
            self.path = path
        elif configured_dsn:
            if not configured_dsn.lower().startswith(postgres_prefixes):
                raise RuntimeError("STRATEX_DATABASE_URL must use PostgreSQL; refusing an implicit local-ledger fallback.")
            self.path = configured_dsn
        else:
            oms_enabled = os.getenv("STRATEX_ORDER_MANAGER_ENABLED", "false").strip().lower() in {
                "1", "true", "yes", "on"
            }
            reconciliation_enabled = os.getenv("STRATEX_RECONCILIATION_ENABLED", "false").strip().lower() in {
                "1", "true", "yes", "on"
            }
            if oms_enabled or reconciliation_enabled:
                raise RuntimeError(
                    "Stratex OMS/reconciliation requires STRATEX_DATABASE_URL pointing "
                    "to the isolated PostgreSQL stratex_db; refusing a local SQLite ledger."
                )
            self.path = os.getenv("STRATEX_ORDER_DB", "state/stratex_orders.sqlite3")
        self._use_postgres = self.path.lower().startswith(
            ("postgresql://", "postgres://", "postgresql+psycopg://")
        )
        self._dsn = self.path
        if self._dsn.startswith("postgresql+psycopg://"):
            self._dsn = "postgresql://" + self._dsn[len("postgresql+psycopg://"):]
        elif self._dsn.startswith("postgres://"):
            self._dsn = "postgresql://" + self._dsn[len("postgres://"):]
        if not self._use_postgres and self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self):
        if self._use_postgres:
            try:
                import psycopg
                from psycopg.rows import dict_row
            except ImportError as exc:  # pragma: no cover - packaging guard
                raise RuntimeError("PostgreSQL order storage requires psycopg 3.") from exc
            return psycopg.connect(self._dsn, row_factory=dict_row, connect_timeout=5)
        conn = sqlite3.connect(self.path, timeout=10.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout=10000")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def _sql(self, statement: str) -> str:
        """Translate query placeholders for psycopg while preserving SQLite SQL."""
        return statement.replace("?", "%s") if self._use_postgres else statement

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
            if self._use_postgres:
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS order_intents (
                        client_order_id TEXT PRIMARY KEY,
                        payload_hash TEXT NOT NULL,
                        payload_json TEXT NOT NULL,
                        status TEXT NOT NULL,
                        result_json TEXT,
                        error_text TEXT,
                        created_at DOUBLE PRECISION NOT NULL,
                        updated_at DOUBLE PRECISION NOT NULL
                    )
                """)
            else:
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
        with self._managed_connection() as conn:
            if self._use_postgres:
                inserted = conn.execute(
                    "INSERT INTO order_intents "
                    "(client_order_id,payload_hash,payload_json,status,created_at,updated_at) "
                    "VALUES (%s,%s,%s,%s,%s,%s) ON CONFLICT (client_order_id) DO NOTHING "
                    "RETURNING client_order_id",
                    (client_order_id, payload_hash, payload_json, "RESERVED", now, now),
                ).fetchone()
                if inserted is not None:
                    return {"kind": "new", "status": "RESERVED"}
                row = conn.execute(
                    "SELECT * FROM order_intents WHERE client_order_id = %s FOR UPDATE",
                    (client_order_id,),
                ).fetchone()
            else:
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
            if row is None:
                raise RuntimeError("Order intent conflict resolved but existing row was not found")
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
        with self._managed_connection() as conn:
            cur = conn.execute(
                self._sql(
                    "UPDATE order_intents SET status=?, result_json=COALESCE(?, result_json), "
                    "error_text=?, updated_at=? WHERE client_order_id=?"
                ),
                (status, result_json, error, now, client_order_id),
            )
            if cur.rowcount != 1:
                raise KeyError(f"Unknown order intent: {client_order_id}")

    def get(self, client_order_id: str) -> dict[str, Any] | None:
        with self._managed_connection() as conn:
            row = conn.execute(
                self._sql("SELECT * FROM order_intents WHERE client_order_id=?"),
                (client_order_id,),
            ).fetchone()
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
                self._sql(
                    "SELECT client_order_id,status,created_at,updated_at,error_text "
                    "FROM order_intents "
                    "WHERE status IN ('RESERVED','SUBMITTING','UNKNOWN','ACCEPTED','PARTIALLY_FILLED') "
                    "ORDER BY created_at ASC LIMIT ?"
                ),
                (limit,),
            ).fetchall()
        return [dict(row) for row in rows]

    def list_unresolved(self, limit: int = 100) -> list[dict[str, Any]]:
        limit = min(1000, max(1, int(limit)))
        with self._managed_connection() as conn:
            rows = conn.execute(
                self._sql(
                    "SELECT client_order_id,status,created_at,updated_at,error_text "
                    "FROM order_intents WHERE status IN ('RESERVED','SUBMITTING','UNKNOWN') "
                    "ORDER BY created_at ASC LIMIT ?"
                ), (limit,)
            ).fetchall()
        return [dict(row) for row in rows]
