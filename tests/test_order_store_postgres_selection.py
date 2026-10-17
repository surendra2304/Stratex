"""Configuration and adapter tests for PostgreSQL-backed OMS storage."""

import pytest

from stratex_agents.order_store import OrderIntentStore


def test_postgres_url_selects_postgres_without_creating_a_local_sqlite_file(monkeypatch, tmp_path):
    dsn = "postgresql://stratex:placeholder@postgres:5432/stratex_db"
    monkeypatch.setenv("STRATEX_DATABASE_URL", dsn)

    # Avoid network access; this verifies backend selection only.
    monkeypatch.setattr(OrderIntentStore, "_initialize", lambda self: None)
    monkeypatch.chdir(tmp_path)
    store = OrderIntentStore()

    assert store._use_postgres is True
    assert store._dsn == dsn
    assert not (tmp_path / "state" / "stratex_orders.sqlite3").exists()


def test_explicit_sqlite_path_remains_available_for_isolated_tests(monkeypatch, tmp_path):
    monkeypatch.setenv("STRATEX_DATABASE_URL", "postgresql://stratex:placeholder@postgres/stratex_db")
    path = tmp_path / "orders.sqlite3"
    store = OrderIntentStore(str(path))

    assert store._use_postgres is False
    assert store.path == str(path)
    assert path.exists()


@pytest.mark.parametrize(
    "enabled_variable",
    ["STRATEX_ORDER_MANAGER_ENABLED", "STRATEX_RECONCILIATION_ENABLED"],
)
def test_enabled_oms_or_reconciliation_refuses_local_sqlite_fallback(
    monkeypatch, tmp_path, enabled_variable
):
    monkeypatch.delenv("STRATEX_DATABASE_URL", raising=False)
    monkeypatch.delenv("STRATEX_ENVIRONMENT", raising=False)
    monkeypatch.delenv("ENVIRONMENT", raising=False)
    monkeypatch.setenv(enabled_variable, "true")
    monkeypatch.chdir(tmp_path)

    with pytest.raises(RuntimeError, match="requires STRATEX_DATABASE_URL"):
        OrderIntentStore()
    assert not (tmp_path / "state" / "stratex_orders.sqlite3").exists()


def test_invalid_configured_stratex_database_url_fails_closed(monkeypatch, tmp_path):
    monkeypatch.setenv("STRATEX_DATABASE_URL", "sqlite:///do-not-use.db")
    monkeypatch.delenv("STRATEX_ENVIRONMENT", raising=False)
    monkeypatch.delenv("ENVIRONMENT", raising=False)
    monkeypatch.chdir(tmp_path)

    with pytest.raises(RuntimeError, match="must use PostgreSQL"):
        OrderIntentStore()
    assert not (tmp_path / "do-not-use.db").exists()
