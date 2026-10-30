"""Backend-selection tests for Oracle Autonomous Database persistence.

These tests never open a network connection: ``_initialize`` is patched so the
constructor only exercises DSN/dialect selection. A live Oracle integration job
must supply a real ``STRATEX_TEST_ORACLE_URL`` separately.
"""

import pytest

from stratex_agents.order_store import OrderIntentStore
from stratex_agents.persistence_backend import redact_dsn
from stratex_agents.portfolio_risk_state import PortfolioRiskStateStore


def test_oracle_url_selects_oracle_without_creating_sqlite(monkeypatch, tmp_path):
    dsn = "oracle+oracledb://STRATEX:placeholder@adb.example.com:1522/STRATEX_LOW"
    monkeypatch.setenv("STRATEX_DATABASE_URL", dsn)
    monkeypatch.setattr(OrderIntentStore, "_initialize", lambda self: None)
    monkeypatch.chdir(tmp_path)

    store = OrderIntentStore()

    assert store._use_oracle is True
    assert store._use_postgres is False
    assert store._dsn == dsn
    assert not (tmp_path / "state" / "stratex_orders.sqlite3").exists()


def test_bare_oracle_url_is_upgraded_to_oracledb_driver(monkeypatch, tmp_path):
    monkeypatch.setenv(
        "STRATEX_DATABASE_URL",
        "oracle://STRATEX:placeholder@adb.example.com:1522/STRATEX_LOW",
    )
    monkeypatch.setattr(OrderIntentStore, "_initialize", lambda self: None)
    monkeypatch.chdir(tmp_path)

    store = OrderIntentStore()

    assert store._use_oracle is True
    assert store._dsn == "oracle+oracledb://STRATEX:placeholder@adb.example.com:1522/STRATEX_LOW"


def test_oracle_url_is_accepted_when_oms_is_enabled(monkeypatch, tmp_path):
    monkeypatch.setenv(
        "STRATEX_DATABASE_URL",
        "oracledb://STRATEX:placeholder@adb.example.com:1522/STRATEX_LOW",
    )
    monkeypatch.setenv("STRATEX_ORDER_MANAGER_ENABLED", "true")
    monkeypatch.setattr(OrderIntentStore, "_initialize", lambda self: None)
    monkeypatch.chdir(tmp_path)

    store = OrderIntentStore()

    assert store._use_oracle is True
    assert store._dsn.startswith("oracle+oracledb://")


def test_portfolio_risk_state_selects_oracle_backend(monkeypatch, tmp_path):
    dsn = "oracle+oracledb://STRATEX:placeholder@adb.example.com:1522/STRATEX_LOW"
    monkeypatch.setenv("STRATEX_DATABASE_URL", dsn)
    monkeypatch.setattr(PortfolioRiskStateStore, "_initialize", lambda self: None)
    monkeypatch.chdir(tmp_path)

    store = PortfolioRiskStateStore()

    assert store._use_oracle is True
    assert store._use_postgres is False
    assert store._dsn == dsn
    assert not (tmp_path / "state" / "portfolio_risk.sqlite3").exists()


def test_redact_dsn_removes_password_and_wallet_password():
    redacted = redact_dsn(
        "oracle+oracledb://STRATEX:super-secret@adb.example.com:1522/STRATEX_LOW"
        "?wallet_password=wallet-secret"
    )

    assert "super-secret" not in redacted
    assert "wallet-secret" not in redacted
    assert "STRATEX:***@adb.example.com" in redacted


def test_explicit_sqlite_path_is_not_treated_as_oracle(monkeypatch, tmp_path):
    monkeypatch.setenv(
        "STRATEX_DATABASE_URL",
        "oracle+oracledb://STRATEX:placeholder@adb.example.com:1522/STRATEX_LOW",
    )
    path = tmp_path / "portfolio.sqlite3"
    store = PortfolioRiskStateStore(str(path))

    assert store._use_oracle is False
    assert store._use_postgres is False
    assert path.exists()


@pytest.mark.parametrize(
    "dsn",
    [
        "mysql://user:pass@host/db",
        "sqlite:///do-not-use.db",
    ],
)
def test_unknown_configured_database_url_still_fails_closed(monkeypatch, tmp_path, dsn):
    monkeypatch.setenv("STRATEX_DATABASE_URL", dsn)
    monkeypatch.chdir(tmp_path)

    with pytest.raises(RuntimeError, match="must use PostgreSQL"):
        OrderIntentStore()

    assert not (tmp_path / "do-not-use.db").exists()
