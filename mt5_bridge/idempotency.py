"""Persistent MT5 bridge idempotency ledger.

An order identifier is reserved before order_send. An ambiguous response is never
silently retried: operators must reconcile the MT5 terminal state first.

Production storage is Oracle Autonomous Database (``oracle+oracledb://``) via
SQLAlchemy 2.x and ``python-oracledb``. A local SQLite file is accepted only when
an isolated test passes an explicit path; production never creates one.
"""
from __future__ import annotations

import json
import os
import sqlite3
from contextlib import contextmanager
import time
from pathlib import Path
from typing import Any

from stratex_agents.persistence_backend import (
    OracleDatabase,
    clob_text,
    detect_dialect,
    normalize_oracle_dsn,
    oracle_error_code,
    require_test_sqlite,
    row_to_dict,
)

_DATABASE_URL_VARIABLES = ("MT5_BRIDGE_DATABASE_URL", "STRATEX_DATABASE_URL", "DATABASE_URL")

_ORACLE_BRIDGE_ORDERS_DDL = """
CREATE TABLE bridge_orders (
    client_order_id VARCHAR2(128 CHAR) NOT NULL,
    request_hash VARCHAR2(128 CHAR) NOT NULL,
    request_json CLOB NOT NULL,
    status VARCHAR2(32 CHAR) NOT NULL,
    response_json CLOB,
    error_text CLOB,
    created_at BINARY_DOUBLE NOT NULL,
    updated_at BINARY_DOUBLE NOT NULL,
    CONSTRAINT pk_bridge_orders PRIMARY KEY (client_order_id)
)
""".strip()


def _configured_dsn() -> str | None:
    for name in _DATABASE_URL_VARIABLES:
        value = (os.getenv(name) or "").strip()
        if value:
            return value
    return None


class DuplicateOrderIdConflict(RuntimeError):
    pass


class UnresolvedBridgeOrder(RuntimeError):
    pass


class BridgeOrderStore:
    TERMINAL_STATES = {"ACCEPTED", "PARTIALLY_FILLED", "FILLED", "REJECTED", "CANCELLED"}

    def __init__(self, path: str | None = None):
        explicit = (path or "").strip() or None
        if explicit is not None:
            self._dialect = detect_dialect(explicit)
            if self._dialect == "oracle":
                self._dsn = normalize_oracle_dsn(explicit)
            else:
                require_test_sqlite(explicit, "The MT5 bridge ledger")
                self._dsn = ""
        else:
            configured = _configured_dsn()
            if not configured:
                raise RuntimeError(
                    "The MT5 bridge ledger requires MT5_BRIDGE_DATABASE_URL, "
                    "STRATEX_DATABASE_URL or DATABASE_URL pointing to an Oracle "
                    "Autonomous Database; SQLite is a test-only fixture."
                )
            if detect_dialect(configured) != "oracle":
                raise RuntimeError(
                    "The MT5 bridge ledger requires an oracle+oracledb:// DSN; "
                    "PostgreSQL, SQLite and other backends are not supported."
                )
            self._dialect = "oracle"
            self._dsn = normalize_oracle_dsn(configured)
        self._use_oracle = self._dialect == "oracle"
        self.path = self._dsn if self._use_oracle else explicit
        if not self._use_oracle and self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._oracle = (
            OracleDatabase(self._dsn, component="MT5 bridge ledger")
            if self._use_oracle else None
        )
        self._initialize()

    def _connect(self):
        if self._use_oracle:  # pragma: no cover - defensive: Oracle uses SQLAlchemy
            raise RuntimeError("Oracle connections are managed through SQLAlchemy; use _managed_connection().")
        conn = sqlite3.connect(self.path, timeout=10.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout=10000")
        return conn

    @contextmanager
    def _managed_connection(self):
        """Close SQLite test connections reliably; Connection.__exit__ only commits/rolls back."""
        if self._use_oracle:
            assert self._oracle is not None
            with self._oracle.connection() as oracle_conn:
                yield oracle_conn
            return
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
        if self._use_oracle:
            assert self._oracle is not None
            with self._oracle.connection() as conn:
                self._oracle.ensure_table(conn, "BRIDGE_ORDERS", _ORACLE_BRIDGE_ORDERS_DDL)
            return
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

    @staticmethod
    def _row_to_result(row: dict[str, Any]) -> dict[str, Any]:
        response_json = clob_text(row.get("response_json"))
        return {
            "kind": "duplicate",
            "status": row["status"],
            "response": json.loads(response_json) if response_json else {},
        }

    def _validate_existing(self, row: dict[str, Any], request_hash: str) -> dict[str, Any]:
        if row["request_hash"] != request_hash:
            raise DuplicateOrderIdConflict("client_order_id was already used for a different payload")
        if row["status"] not in self.TERMINAL_STATES:
            raise UnresolvedBridgeOrder(
                f"order {row['client_order_id']} is {row['status']}; do not resubmit until reconciled"
            )
        return self._row_to_result(row)

    def lookup(self, client_order_id: str, request_hash: str) -> dict[str, Any] | None:
        """Return a cached terminal result before dynamic broker checks; do not reserve new IDs."""
        with self._managed_connection() as conn:
            if self._use_oracle:
                assert self._oracle is not None
                row = self._oracle.fetchone(
                    conn,
                    "SELECT * FROM bridge_orders WHERE client_order_id = :client_order_id",
                    {"client_order_id": client_order_id},
                )
            else:
                fetched = conn.execute(
                    "SELECT * FROM bridge_orders WHERE client_order_id=?", (client_order_id,)
                ).fetchone()
                row = row_to_dict(fetched) if fetched is not None else None
        if row is None:
            return None
        return self._validate_existing(row, request_hash)

    def reserve(self, client_order_id: str, request_hash: str, request: dict[str, Any]) -> dict[str, Any]:
        now = time.time()
        payload = json.dumps(request, sort_keys=True, separators=(",", ":"), allow_nan=False)
        if self._use_oracle:
            return self._reserve_oracle(client_order_id, request_hash, payload, now)
        with self._managed_connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT * FROM bridge_orders WHERE client_order_id=?", (client_order_id,)
            ).fetchone()
            if row:
                return self._validate_existing(row_to_dict(row), request_hash)
            conn.execute(
                "INSERT INTO bridge_orders "
                "(client_order_id,request_hash,request_json,status,created_at,updated_at) "
                "VALUES (?,?,?,?,?,?)",
                (client_order_id, request_hash, payload, "SUBMITTING", now, now),
            )
            return {"kind": "new", "status": "SUBMITTING"}

    def _reserve_oracle(self, client_order_id: str, request_hash: str,
                        payload: str, now: float) -> dict[str, Any]:
        assert self._oracle is not None
        insert_sql = (
            "INSERT INTO bridge_orders "
            "(client_order_id,request_hash,request_json,status,created_at,updated_at) "
            "VALUES (:client_order_id,:request_hash,:request_json,"
            ":status,:created_at,:updated_at)"
        )
        params = {
            "client_order_id": client_order_id,
            "request_hash": request_hash,
            "request_json": payload,
            "status": "SUBMITTING",
            "created_at": now,
            "updated_at": now,
        }
        with self._oracle.connection() as conn:
            row: dict[str, Any] | None = None
            for _attempt in range(2):
                try:
                    self._oracle.execute(conn, insert_sql, params)
                except Exception as exc:
                    if oracle_error_code(exc) != "ORA-00001":
                        raise
                else:
                    return {"kind": "new", "status": "SUBMITTING"}
                row = self._oracle.fetchone(
                    conn,
                    "SELECT * FROM bridge_orders WHERE client_order_id = :client_order_id FOR UPDATE",
                    {"client_order_id": client_order_id},
                )
                if row is not None:
                    break
            if row is None:
                raise RuntimeError("MT5 bridge reservation conflicted but no existing row was found")
            return self._validate_existing(row, request_hash)

    def finish(self, client_order_id: str, status: str, response: dict[str, Any] | None = None,
               error: str | None = None) -> None:
        if status not in {"ACCEPTED", "FILLED", "REJECTED", "CANCELLED", "UNKNOWN"}:
            raise ValueError(f"Invalid MT5 bridge order state: {status}")
        response_json = json.dumps(response, sort_keys=True, separators=(",", ":"), allow_nan=False) if response is not None else None
        if self._use_oracle:
            assert self._oracle is not None
            with self._oracle.connection() as conn:
                row = self._oracle.fetchone(
                    conn,
                    "SELECT response_json FROM bridge_orders "
                    "WHERE client_order_id = :client_order_id FOR UPDATE",
                    {"client_order_id": client_order_id},
                )
                if row is None:
                    raise KeyError(f"Unknown MT5 client order id: {client_order_id}")
                effective_response = (
                    response_json if response_json is not None
                    else clob_text(row.get("response_json"))
                )
                cur = self._oracle.execute(
                    conn,
                    "UPDATE bridge_orders SET status=:status, response_json=:response_json, "
                    "error_text=:error_text, updated_at=:updated_at "
                    "WHERE client_order_id=:client_order_id",
                    {
                        "status": status,
                        "response_json": effective_response,
                        "error_text": error,
                        "updated_at": time.time(),
                        "client_order_id": client_order_id,
                    },
                )
                if cur.rowcount != 1:
                    raise KeyError(f"Unknown MT5 client order id: {client_order_id}")
            return
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
            if self._use_oracle:
                assert self._oracle is not None
                row = self._oracle.fetchone(
                    conn,
                    "SELECT * FROM bridge_orders WHERE client_order_id = :client_order_id",
                    {"client_order_id": client_order_id},
                )
            else:
                fetched = conn.execute(
                    "SELECT * FROM bridge_orders WHERE client_order_id=?", (client_order_id,)
                ).fetchone()
                row = row_to_dict(fetched) if fetched is not None else None
        if row is None:
            return None
        result = dict(row)
        response_json = clob_text(result.pop("response_json", None))
        result["response"] = json.loads(response_json) if response_json else None
        result.pop("request_hash", None)
        result.pop("request_json", None)
        result["error_text"] = clob_text(result.get("error_text"))
        return result
