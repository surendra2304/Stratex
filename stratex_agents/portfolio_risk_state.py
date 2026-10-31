"""Persistent daily baseline and high-water state for portfolio risk checks.

Only broker/account equity snapshots that are already normalized to one reporting
currency should be passed here. The store does not infer PnL or currency rates.

Backend: Oracle Autonomous Database 26ai through SQLAlchemy 2.x and
``python-oracledb``. A local SQLite file is available only when an isolated test
passes an explicit path; a production process refuses one. PostgreSQL is not
supported. The public contract is identical for both paths.
"""
from __future__ import annotations

import logging
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from .persistence_backend import (
    OracleDatabase,
    configured_dsn,
    detect_dialect,
    normalize_oracle_dsn,
    oracle_error_code,
    redact_dsn,
    require_test_sqlite,
    row_to_dict,
)

logger = logging.getLogger(__name__)

_ORACLE_PORTFOLIO_EQUITY_STATE_DDL = """
CREATE TABLE portfolio_equity_state (
    account_key VARCHAR2(256 CHAR) NOT NULL,
    reporting_currency VARCHAR2(32 CHAR) NOT NULL,
    utc_day VARCHAR2(10 CHAR) NOT NULL,
    day_start_equity BINARY_DOUBLE NOT NULL,
    high_water_equity BINARY_DOUBLE NOT NULL,
    updated_at BINARY_DOUBLE NOT NULL,
    CONSTRAINT pk_portfolio_equity_state
        PRIMARY KEY (account_key, reporting_currency, utc_day)
)
""".strip()


class PortfolioRiskStateError(RuntimeError):
    pass


class PortfolioRiskStateStore:
    def __init__(self, path: str | None = None):
        explicit = (path or "").strip() or None
        if explicit is not None:
            self._dialect = detect_dialect(explicit)
            if self._dialect == "oracle":
                self._dsn = normalize_oracle_dsn(explicit)
            else:
                require_test_sqlite(explicit, "The Stratex portfolio risk store")
                self._dsn = ""
        else:
            configured = configured_dsn()
            if not configured:
                raise RuntimeError(
                    "Portfolio risk state requires STRATEX_DATABASE_URL (or DATABASE_URL) "
                    "pointing to an Oracle Autonomous Database; SQLite and PostgreSQL are "
                    "not supported production backends. Pass an explicit path only in "
                    "isolated tests."
                )
            if detect_dialect(configured) != "oracle":
                raise RuntimeError(
                    "STRATEX_DATABASE_URL must be an oracle+oracledb:// DSN; PostgreSQL, "
                    "SQLite and other backends are not supported production backends."
                )
            self._dialect = "oracle"
            self._dsn = normalize_oracle_dsn(configured)
        self._use_oracle = self._dialect == "oracle"
        self.path = self._dsn if self._use_oracle else explicit
        if not self._use_oracle and self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._oracle = (
            OracleDatabase(self._dsn, component="portfolio risk state")
            if self._use_oracle else None
        )
        self._initialize()

    def _connect(self):
        if self._use_oracle:  # pragma: no cover - defensive: Oracle uses SQLAlchemy
            raise RuntimeError("Oracle connections are managed through SQLAlchemy; use _managed_connection().")
        conn = sqlite3.connect(self.path, timeout=10.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout=10000")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    @contextmanager
    def _managed_connection(self):
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

    def _initialize(self):
        if self._use_oracle:
            self._initialize_oracle()
            return
        with self._managed_connection() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS portfolio_equity_state (
                    account_key TEXT NOT NULL,
                    reporting_currency TEXT NOT NULL,
                    utc_day TEXT NOT NULL,
                    day_start_equity REAL NOT NULL,
                    high_water_equity REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    PRIMARY KEY (account_key, reporting_currency, utc_day)
                )
            """)

    def _initialize_oracle(self) -> None:
        """Idempotently create the Oracle baseline table in the current schema."""
        assert self._oracle is not None
        with self._oracle.connection() as conn:
            schema = self._oracle.current_schema(conn)
            logger.info(
                "Stratex portfolio risk state Oracle bootstrap: schema=%s target=%s",
                schema,
                redact_dsn(self._dsn),
            )
            self._oracle.ensure_table(
                conn, "PORTFOLIO_EQUITY_STATE", _ORACLE_PORTFOLIO_EQUITY_STATE_DDL
            )

    def observe(
        self,
        *,
        account_key: str,
        reporting_currency: str,
        equity: float,
        timestamp: float | None = None,
    ) -> dict[str, Any]:
        """Persist the UTC-day baseline and high-water mark for a verified equity sample."""
        import datetime

        key = str(account_key).strip()
        currency = str(reporting_currency).strip().upper()
        value = float(equity)
        now = float(timestamp if timestamp is not None else time.time())
        if not key or not currency or len(currency) > 12:
            raise PortfolioRiskStateError("account_key and reporting_currency are required")
        if not all(map(lambda n: n == n and abs(n) != float("inf"), (value, now))) or value <= 0:
            raise PortfolioRiskStateError("equity must be positive and timestamps/equity finite")
        day = datetime.datetime.fromtimestamp(now, datetime.timezone.utc).date().isoformat()
        with self._managed_connection() as conn:
            if self._use_oracle:
                day_start, high_water = self._observe_oracle(
                    conn, key=key, currency=currency, day=day, value=value, now=now
                )
            else:
                row = conn.execute(
                    "SELECT day_start_equity, high_water_equity FROM portfolio_equity_state "
                    "WHERE account_key=? AND reporting_currency=? AND utc_day=?",
                    (key, currency, day),
                ).fetchone()
                if row is None:
                    day_start = value
                    high_water = value
                    conn.execute(
                        "INSERT INTO portfolio_equity_state(account_key, reporting_currency, utc_day, "
                        "day_start_equity, high_water_equity, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
                        (key, currency, day, day_start, high_water, now),
                    )
                else:
                    day_start = float(row["day_start_equity"])
                    high_water = max(float(row["high_water_equity"]), value)
                    conn.execute(
                        "UPDATE portfolio_equity_state SET high_water_equity=?, updated_at=? "
                        "WHERE account_key=? AND reporting_currency=? AND utc_day=?",
                        (high_water, now, key, currency, day),
                    )
        return {
            "utc_day": day,
            "day_start_equity": day_start,
            "high_water_equity": high_water,
            "equity": value,
            "reporting_currency": currency,
            "captured_at": now,
            "equity_basis": "account_equity",
        }

    def _observe_oracle(self, conn, *, key: str, currency: str, day: str,
                        value: float, now: float) -> tuple[float, float]:
        """Atomic Oracle upsert of the daily baseline and high-water mark.

        ``MERGE`` performs the select-then-insert/update atomically: the insert
        branch keeps the first observed equity as the day baseline, while the
        update branch only raises the high-water mark. The row is then read back
        under the same transaction/lock. A concurrent first observation can make
        Oracle raise ORA-00001 for the uncommitted duplicate; the statement is
        retried once, at which point it takes the MATCHED branch.
        """
        assert self._oracle is not None
        merge_sql = (
            "MERGE INTO portfolio_equity_state t "
            "USING dual "
            "ON (t.account_key = :account_key "
            "AND t.reporting_currency = :reporting_currency "
            "AND t.utc_day = :utc_day) "
            "WHEN MATCHED THEN UPDATE SET "
            "t.high_water_equity = GREATEST(t.high_water_equity, :equity), "
            "t.updated_at = :updated_at "
            "WHEN NOT MATCHED THEN INSERT "
            "(account_key, reporting_currency, utc_day, day_start_equity, high_water_equity, updated_at) "
            "VALUES (:account_key, :reporting_currency, :utc_day, :equity, :equity, :updated_at)"
        )
        select_sql = (
            "SELECT day_start_equity, high_water_equity FROM portfolio_equity_state "
            "WHERE account_key = :account_key AND reporting_currency = :reporting_currency "
            "AND utc_day = :utc_day"
        )
        params = {
            "account_key": key,
            "reporting_currency": currency,
            "utc_day": day,
            "equity": value,
            "updated_at": now,
        }
        row: dict[str, Any] | None = None
        for attempt in range(2):
            try:
                self._oracle.execute(conn, merge_sql, params)
            except Exception as exc:
                if oracle_error_code(exc) != "ORA-00001" or attempt == 1:
                    raise
                continue
            row = self._oracle.fetchone(conn, select_sql, params)
            if row is not None:
                return float(row["day_start_equity"]), float(row["high_water_equity"])
            raise PortfolioRiskStateError(
                "Oracle merge completed but the portfolio baseline row was not readable"
            )
        raise PortfolioRiskStateError("Oracle portfolio baseline upsert did not converge")

    def get_baseline(
        self,
        *,
        account_key: str,
        reporting_currency: str,
        utc_day: str | None = None,
    ) -> dict[str, Any] | None:
        """Fetch the persistent baseline for an account and currency."""
        key = str(account_key).strip()
        currency = str(reporting_currency).strip().upper()
        if not key or not currency:
            raise PortfolioRiskStateError("account_key and reporting_currency are required")
        if utc_day is None:
            import datetime
            utc_day = datetime.datetime.now(datetime.timezone.utc).date().isoformat()
        with self._managed_connection() as conn:
            if self._use_oracle:
                assert self._oracle is not None
                row = self._oracle.fetchone(
                    conn,
                    "SELECT day_start_equity, high_water_equity, updated_at "
                    "FROM portfolio_equity_state "
                    "WHERE account_key = :account_key "
                    "AND reporting_currency = :reporting_currency AND utc_day = :utc_day",
                    {"account_key": key, "reporting_currency": currency, "utc_day": utc_day},
                )
            else:
                fetched = conn.execute(
                    "SELECT day_start_equity, high_water_equity, updated_at FROM portfolio_equity_state "
                    "WHERE account_key=? AND reporting_currency=? AND utc_day=?",
                    (key, currency, utc_day),
                ).fetchone()
                row = row_to_dict(fetched) if fetched is not None else None
        if row is None:
            return None
        return {
            "account_key": key,
            "reporting_currency": currency,
            "utc_day": utc_day,
            "day_start_equity": float(row["day_start_equity"]),
            "high_water_equity": float(row["high_water_equity"]),
            "updated_at": float(row["updated_at"]),
        }

    def enrich_snapshot(self, snapshot: dict[str, Any], *, account_key: str) -> dict[str, Any]:
        """Return a copy with persistent risk baselines; refuse estimated/degraded equity."""
        if not isinstance(snapshot, dict) or snapshot.get("status") != "healthy":
            raise PortfolioRiskStateError("A healthy portfolio snapshot is required")
        if snapshot.get("currency_conversion_complete") is not True:
            raise PortfolioRiskStateError("Complete explicit reporting-currency conversion is required")
        if snapshot.get("equity_basis") != "account_equity":
            raise PortfolioRiskStateError("Verified broker account equity is required; balance totals are not enough")
        equity = snapshot.get("reporting_equity")
        enriched = dict(snapshot)
        enriched.update(self.observe(
            account_key=account_key,
            reporting_currency=snapshot.get("reporting_currency", ""),
            equity=equity,
            timestamp=snapshot.get("captured_at"),
        ))
        return enriched
