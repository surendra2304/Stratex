"""Configuration and adapter tests for PostgreSQL-backed OMS storage."""

from pathlib import Path

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
