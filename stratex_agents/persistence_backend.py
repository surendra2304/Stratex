"""Oracle Autonomous Database support for the Stratex persistence stores.

Oracle Autonomous Database 26ai is the only supported production backend and is
reached through SQLAlchemy 2.x and ``python-oracledb``. A local SQLite file is
accepted only when an isolated test passes an explicit filesystem path; it is
never an implicit substitute for a configured DSN and is never opened outside a
test process. PostgreSQL is not supported at all.

SQLAlchemy and ``oracledb`` are imported lazily, so importing a store module does
not require the Oracle driver until an Oracle DSN is actually selected.
"""
from __future__ import annotations

import logging
import os
import re
import sys
from contextlib import contextmanager
from typing import Any
from urllib.parse import urlsplit, urlunsplit

logger = logging.getLogger(__name__)

ORACLE_PREFIXES = ("oracle://", "oracle+oracledb://", "oracledb://")
DATABASE_URL_VARIABLES = ("STRATEX_DATABASE_URL", "DATABASE_URL")

_ORA_CODE = re.compile(r"\b(ORA-\d{5})\b")
_WALLET_PASSWORD = re.compile(r"(wallet_password=)[^&]*", re.IGNORECASE)


def detect_dialect(value: str) -> str:
    """Return ``"oracle"`` for an Oracle DSN, otherwise ``"sqlite"`` (a path)."""
    lowered = (value or "").strip().lower()
    return "oracle" if lowered.startswith(ORACLE_PREFIXES) else "sqlite"


def configured_dsn() -> str | None:
    """Return the first configured database URL (``STRATEX_DATABASE_URL`` first)."""
    for name in DATABASE_URL_VARIABLES:
        value = (os.getenv(name) or "").strip()
        if value:
            return value
    return None


def running_under_tests() -> bool:
    """Return True only inside an isolated test process.

    SQLite is a test fixture, never a runtime backend. Production processes never
    satisfy this check, so an explicit filesystem path fails closed there.
    """
    if "pytest" in sys.modules:
        return True
    environment = (os.getenv("STRATEX_ENV") or os.getenv("FRIDAY_ENV") or "").strip().lower()
    return environment in {"test", "testing"}


def require_test_sqlite(path: str, component: str) -> None:
    """Refuse a filesystem database path outside an isolated test process."""
    if running_under_tests():
        return
    raise RuntimeError(
        f"{component} accepts a local SQLite path only in isolated tests; "
        "configure STRATEX_DATABASE_URL (or DATABASE_URL) with an "
        "oracle+oracledb:// Autonomous Database DSN for runtime use."
    )


def normalize_oracle_dsn(value: str) -> str:
    """Upgrade bare Oracle schemes to SQLAlchemy's ``oracle+oracledb`` dialect.

    ``oracle://`` defaults to the cx_Oracle driver in SQLAlchemy 2.x. Stratex
    ships the thin ``oracledb`` driver only, so both spellings are normalized.
    """
    scheme, separator, rest = value.partition("://")
    if separator and scheme.lower() in {"oracle", "oracledb"}:
        return "oracle+oracledb://" + rest
    return value


def redact_dsn(value: str) -> str:
    """Return a log-safe DSN with passwords and wallet passwords removed."""
    try:
        parts = urlsplit(value)
    except ValueError:
        return "<unparseable dsn>"
    if not parts.scheme or not parts.netloc:
        return value
    credentials, separator, host = parts.netloc.rpartition("@")
    if separator:
        user = credentials.split(":", 1)[0]
        netloc = f"{user}:***@{host}"
    else:
        netloc = parts.netloc
    query = _WALLET_PASSWORD.sub(r"\1***", parts.query)
    return urlunsplit((parts.scheme, netloc, parts.path, query, parts.fragment))


def oracle_error_code(exc: BaseException) -> str | None:
    """Extract an ``ORA-nnnnn`` code from a SQLAlchemy/oracledb exception chain."""
    candidates = (exc, getattr(exc, "orig", None), getattr(exc, "__cause__", None))
    for candidate in candidates:
        if candidate is None:
            continue
        code = getattr(candidate, "code", None)
        if code:
            match = _ORA_CODE.search(str(code))
            if match:
                return match.group(1)
        match = _ORA_CODE.search(str(candidate))
        if match:
            return match.group(1)
    return None


def row_to_dict(row: Any) -> dict[str, Any]:
    """Normalize a SQLite row or SQLAlchemy Row to lowercase keys.

    Oracle returns unquoted identifiers in upper case while SQLite returns them
    in lower case. This keeps the public result contract identical.
    """
    mapping = getattr(row, "_mapping", None)
    items = mapping.items() if mapping is not None else dict(row).items()
    return {str(key).lower(): value for key, value in items}


def clob_text(value: Any) -> str | None:
    """Return CLOB text as ``str`` even if the driver yields a LOB object."""
    if value is None:
        return None
    if isinstance(value, str):
        return value
    if isinstance(value, bytes):
        return value.decode("utf-8")
    read = getattr(value, "read", None)
    if callable(read):
        return read()
    return str(value)


class OracleDatabase:
    """Small SQLAlchemy 2.x wrapper around one Oracle Autonomous Database DSN.

    The wrapper deliberately owns no business logic. It supplies transactional
    connections, named-parameter execution, row normalization and idempotent
    schema bootstrap helpers shared by the order and portfolio-risk stores.
    """

    def __init__(self, dsn: str, *, component: str):
        self.dsn = dsn
        self.component = component
        self._engine: Any | None = None
        self._schema: str | None = None

    def _get_engine(self):
        if self._engine is not None:
            return self._engine
        try:
            import oracledb  # noqa: F401  (driver registration / availability check)
            from sqlalchemy import create_engine
        except ImportError as exc:  # pragma: no cover - packaging guard
            raise RuntimeError(
                "Oracle persistence requires SQLAlchemy 2.x and the oracledb driver "
                "(26.x); no Oracle connection was attempted."
            ) from exc
        self._engine = create_engine(
            self.dsn,
            pool_pre_ping=True,
            pool_size=2,
            max_overflow=3,
            pool_recycle=1800,
        )
        logger.info(
            "Stratex %s persistence: Oracle Autonomous Database configured for %s",
            self.component,
            redact_dsn(self.dsn),
        )
        return self._engine

    @contextmanager
    def connection(self):
        """Yield one SQLAlchemy connection inside an explicit transaction."""
        engine = self._get_engine()
        conn = engine.connect()
        transaction = conn.begin()
        try:
            yield conn
        except Exception:
            transaction.rollback()
            raise
        else:
            transaction.commit()
        finally:
            conn.close()

    @staticmethod
    def _text(statement: str):
        from sqlalchemy import text

        return text(statement)

    def execute(self, conn, statement: str, params: dict[str, Any] | None = None):
        return conn.execute(self._text(statement), params or {})

    def fetchone(self, conn, statement: str, params: dict[str, Any] | None = None) -> dict[str, Any] | None:
        row = conn.execute(self._text(statement), params or {}).fetchone()
        return row_to_dict(row) if row is not None else None

    def fetchall(self, conn, statement: str, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        rows = conn.execute(self._text(statement), params or {}).fetchall()
        return [row_to_dict(row) for row in rows]

    def current_schema(self, conn) -> str:
        if self._schema is None:
            self._schema = str(
                conn.execute(
                    self._text("SELECT SYS_CONTEXT('USERENV','CURRENT_SCHEMA') AS current_schema FROM dual")
                ).scalar_one()
            )
        return self._schema

    def object_exists(self, conn, view: str, name_column: str, name: str) -> bool:
        """Check an Oracle data-dictionary view for an object in the current schema.

        ``view`` and ``name_column`` are internal constants, never caller input.
        """
        statement = (
            f"SELECT COUNT(*) AS object_count FROM {view} "
            "WHERE OWNER = SYS_CONTEXT('USERENV','CURRENT_SCHEMA') "
            f"AND {name_column} = :object_name"
        )
        count = conn.execute(self._text(statement), {"object_name": name.upper()}).scalar_one()
        return int(count) > 0

    def _object_label(self, conn, name: str) -> str:
        return f"{self.current_schema(conn)}.{name.upper()}"

    def ensure_table(self, conn, table: str, ddl: str) -> None:
        if self.object_exists(conn, "ALL_TABLES", "TABLE_NAME", table):
            logger.debug(
                "Stratex %s persistence: table %s already present",
                self.component,
                self._object_label(conn, table),
            )
            return
        try:
            conn.exec_driver_sql(ddl)
        except Exception as exc:
            # Two workers may bootstrap at the same time. ORA-00955 is benign
            # only when the object is now visible; every other failure is fatal.
            if oracle_error_code(exc) == "ORA-00955" and self.object_exists(
                conn, "ALL_TABLES", "TABLE_NAME", table
            ):
                logger.info(
                    "Stratex %s persistence: table %s was created concurrently",
                    self.component,
                    self._object_label(conn, table),
                )
                return
            logger.error(
                "Stratex %s persistence: failed to create Oracle table %s: %s",
                self.component,
                self._object_label(conn, table),
                exc,
            )
            raise
        logger.info(
            "Stratex %s persistence: created Oracle table %s",
            self.component,
            self._object_label(conn, table),
        )

    def ensure_index(self, conn, index: str, ddl: str) -> None:
        if self.object_exists(conn, "ALL_INDEXES", "INDEX_NAME", index):
            logger.debug(
                "Stratex %s persistence: index %s already present",
                self.component,
                self._object_label(conn, index),
            )
            return
        try:
            conn.exec_driver_sql(ddl)
        except Exception as exc:
            if oracle_error_code(exc) == "ORA-00955" and self.object_exists(
                conn, "ALL_INDEXES", "INDEX_NAME", index
            ):
                logger.info(
                    "Stratex %s persistence: index %s was created concurrently",
                    self.component,
                    self._object_label(conn, index),
                )
                return
            logger.error(
                "Stratex %s persistence: failed to create Oracle index %s: %s",
                self.component,
                self._object_label(conn, index),
                exc,
            )
            raise
        logger.info(
            "Stratex %s persistence: created Oracle index %s",
            self.component,
            self._object_label(conn, index),
        )

    def ensure_trigger(self, conn, trigger: str, ddl: str) -> None:
        if self.object_exists(conn, "ALL_TRIGGERS", "TRIGGER_NAME", trigger):
            logger.debug(
                "Stratex %s persistence: trigger %s already present",
                self.component,
                self._object_label(conn, trigger),
            )
            return
        try:
            conn.exec_driver_sql(ddl)
        except Exception as exc:
            logger.error(
                "Stratex %s persistence: failed to create Oracle trigger %s: %s",
                self.component,
                self._object_label(conn, trigger),
                exc,
            )
            raise
        logger.info(
            "Stratex %s persistence: created Oracle trigger %s",
            self.component,
            self._object_label(conn, trigger),
        )
