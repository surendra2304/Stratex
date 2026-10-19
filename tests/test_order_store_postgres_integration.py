"""Integration tests for the PostgreSQL-backed Stratex OMS ledger.

The GitHub Actions job supplies STRATEX_TEST_POSTGRES_URL from an isolated,
ephemeral PostgreSQL 18 service. These tests never contact an exchange or
enable trade execution.
"""

from __future__ import annotations

import os
import uuid

import pytest

from stratex_agents.order_store import (
    IntentConflictError,
    IntentInFlightError,
    OrderIntentStore,
)


@pytest.fixture
def postgres_store():
    dsn = os.getenv("STRATEX_TEST_POSTGRES_URL")
    if not dsn:
        pytest.skip("STRATEX_TEST_POSTGRES_URL is only supplied by the PostgreSQL integration job")
    store = OrderIntentStore(dsn)
    # This test database is dedicated to the job. Clear state between test cases.
    with store._managed_connection() as conn:
        conn.execute("DELETE FROM order_intents")
    return store


def test_postgres_ledger_reserves_persists_and_returns_duplicate_result(postgres_store):
    client_id = f"pg-{uuid.uuid4().hex}"
    payload = {"venue": "binance", "symbol": "BTCUSDT", "quantity": 0.001}
    payload_hash = "hash-" + uuid.uuid4().hex

    assert postgres_store.reserve(client_id, payload_hash, payload) == {
        "kind": "new",
        "status": "RESERVED",
    }
    postgres_store.transition(
        client_id,
        "ACCEPTED",
        result={"status": "ACCEPTED", "venue_order_id": "demo-order-1"},
    )

    stored = postgres_store.get(client_id)
    assert stored is not None
    assert stored["status"] == "ACCEPTED"
    assert stored["payload"] == payload
    assert stored["result"] == {"status": "ACCEPTED", "venue_order_id": "demo-order-1"}

    duplicate = postgres_store.reserve(client_id, payload_hash, payload)
    assert duplicate["kind"] == "duplicate"
    assert duplicate["status"] == "ACCEPTED"
    assert duplicate["result"]["venue_order_id"] == "demo-order-1"


def test_postgres_ledger_rejects_id_reuse_for_different_intent(postgres_store):
    client_id = f"pg-{uuid.uuid4().hex}"
    original = {"symbol": "BTCUSDT", "quantity": 0.001}
    assert postgres_store.reserve(client_id, "payload-a", original)["kind"] == "new"

    with pytest.raises(IntentConflictError):
        postgres_store.reserve(client_id, "payload-b", {"symbol": "BTCUSDT", "quantity": 0.002})


def test_postgres_ledger_refuses_resubmission_while_unresolved(postgres_store):
    client_id = f"pg-{uuid.uuid4().hex}"
    payload = {"symbol": "ETHUSDT", "quantity": 0.01}
    postgres_store.reserve(client_id, "hash", payload)

    with pytest.raises(IntentInFlightError):
        postgres_store.reserve(client_id, "hash", payload)

    postgres_store.transition(client_id, "UNKNOWN", error="demo response timed out")
    with pytest.raises(IntentInFlightError):
        postgres_store.reserve(client_id, "hash", payload)


def test_postgres_ledger_lists_reconcilable_intents_and_bounds_limit(postgres_store):
    first = f"pg-{uuid.uuid4().hex}"
    second = f"pg-{uuid.uuid4().hex}"
    third = f"pg-{uuid.uuid4().hex}"
    postgres_store.reserve(first, "hash-1", {"symbol": "BTCUSDT"})
    postgres_store.reserve(second, "hash-2", {"symbol": "ETHUSDT"})
    postgres_store.reserve(third, "hash-3", {"symbol": "SOLUSDT"})
    postgres_store.transition(second, "ACCEPTED", result={"status": "ACCEPTED"})
    postgres_store.transition(third, "REJECTED", error="risk gate rejected")

    unresolved = {row["client_order_id"] for row in postgres_store.list_unresolved(limit=100)}
    assert unresolved == {first}

    reconcilable = {row["client_order_id"] for row in postgres_store.list_reconcilable(limit=100)}
    assert reconcilable == {first, second}

    assert len(postgres_store.list_reconcilable(limit=5000)) <= 1000
