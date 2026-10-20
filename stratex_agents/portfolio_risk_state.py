"""Persistent daily baseline and high-water state for portfolio risk checks.

Only broker/account equity snapshots that are already normalized to one reporting
currency should be passed here. The store does not infer PnL or currency rates.
Supports both PostgreSQL (dokploy-postgres) and SQLite backends.
"""
from __future__ import annotations

import os
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any


class PortfolioRiskStateError(RuntimeError):
    pass


class PortfolioRiskStateStore:
    def __init__(self, path: str | None = None):
        configured_dsn = (os.getenv("STRATEX_DATABASE_URL") or "").strip()
        postgres_prefixes = ("postgresql://", "postgres://", "postgresql+psycopg://")
        if path is not None:
            self.path = path
        elif configured_dsn:
            if not configured_dsn.lower().startswith(postgres_prefixes):
                raise RuntimeError("STRATEX_DATABASE_URL must use PostgreSQL; refusing an implicit local-ledger fallback.")
            self.path = configured_dsn
        else:
            self.path = os.getenv("STRATEX_PORTFOLIO_RISK_DB", "state/portfolio_risk.sqlite3")

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
            except ImportError as exc:
                raise RuntimeError("PostgreSQL storage requires psycopg 3.") from exc
            return psycopg.connect(self._dsn, row_factory=dict_row, connect_timeout=5)
        conn = sqlite3.connect(self.path, timeout=10.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout=10000")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def _sql(self, statement: str) -> str:
        return statement.replace("?", "%s") if self._use_postgres else statement

    @contextmanager
    def _managed_connection(self):
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
        with self._managed_connection() as conn:
            if self._use_postgres:
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS portfolio_equity_state (
                        account_key TEXT NOT NULL,
                        reporting_currency TEXT NOT NULL,
                        utc_day TEXT NOT NULL,
                        day_start_equity DOUBLE PRECISION NOT NULL,
                        high_water_equity DOUBLE PRECISION NOT NULL,
                        updated_at DOUBLE PRECISION NOT NULL,
                        PRIMARY KEY (account_key, reporting_currency, utc_day)
                    )
                """)
            else:
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
            row = conn.execute(
                self._sql("SELECT day_start_equity, high_water_equity FROM portfolio_equity_state "
                          "WHERE account_key=? AND reporting_currency=? AND utc_day=?"),
                (key, currency, day),
            ).fetchone()
            if row is None:
                day_start = value
                high_water = value
                conn.execute(
                    self._sql("INSERT INTO portfolio_equity_state(account_key, reporting_currency, utc_day, "
                              "day_start_equity, high_water_equity, updated_at) VALUES (?, ?, ?, ?, ?, ?)"),
                    (key, currency, day, day_start, high_water, now),
                )
            else:
                day_start = float(row["day_start_equity"])
                high_water = max(float(row["high_water_equity"]), value)
                conn.execute(
                    self._sql("UPDATE portfolio_equity_state SET high_water_equity=?, updated_at=? "
                              "WHERE account_key=? AND reporting_currency=? AND utc_day=?"),
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
            row = conn.execute(
                self._sql("SELECT day_start_equity, high_water_equity, updated_at FROM portfolio_equity_state "
                          "WHERE account_key=? AND reporting_currency=? AND utc_day=?"),
                (key, currency, utc_day),
            ).fetchone()
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
