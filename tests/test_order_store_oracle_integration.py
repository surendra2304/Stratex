"""Integration tests for the Oracle Autonomous Database persistence path.

These run only when ``STRATEX_TEST_ORACLE_URL`` points at a disposable schema
owned by the ``STRATEX`` test user. Never point this at production: the tests
create intents, audit events and portfolio baseline rows.

The audit ledger is append-only, so every test uses fresh IDs rather than
deleting rows between cases.
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
from stratex_agents.portfolio_risk_state import PortfolioRiskStateStore


@pytest.fixture
def oracle_store():
    dsn = os.getenv("STRATEX_TEST_ORACLE_URL")
    if not dsn:
        pytest.skip("STRATEX_TEST_ORACLE_URL is only supplied by the Oracle integration job")
    return OrderIntentStore(dsn)


@pytest.fixture
def oracle_risk_store():
    dsn = os.getenv("STRATEX_TEST_ORACLE_URL")
    if not dsn:
        pytest.skip("STRATEX_TEST_ORACLE_URL is only supplied by the Oracle integration job")
    return PortfolioRiskStateStore(dsn)


def test_oracle_ledger_reserves_persists_and_returns_duplicate_result(oracle_store):
    client_id = f"ora-{uuid.uuid4().hex}"
    payload = {"venue": "binance", "symbol": "BTCUSDT", "quantity": 0.001}
    payload_hash = "hash-" + uuid.uuid4().hex

    assert oracle_store.reserve(client_id, payload_hash, payload) == {
        "kind": "new",
        "status": "RESERVED",
    }
    oracle_store.transition(
        client_id,
        "ACCEPTED",
        result={"status": "ACCEPTED", "venue_order_id": "demo-order-1"},
    )

    stored = oracle_store.get(client_id)
    assert stored is not None
    assert stored["status"] == "ACCEPTED"
    assert stored["payload"] == payload
    assert stored["result"] == {"status": "ACCEPTED", "venue_order_id": "demo-order-1"}

    duplicate = oracle_store.reserve(client_id, payload_hash, payload)
    assert duplicate["kind"] == "duplicate"
    assert duplicate["status"] == "ACCEPTED"
    assert duplicate["result"]["venue_order_id"] == "demo-order-1"


def test_oracle_ledger_rejects_id_reuse_and_in_flight_resubmission(oracle_store):
    client_id = f"ora-{uuid.uuid4().hex}"
    original = {"symbol": "BTCUSDT", "quantity": 0.001}
    assert oracle_store.reserve(client_id, "payload-a", original)["kind"] == "new"

    with pytest.raises(IntentConflictError):
        oracle_store.reserve(client_id, "payload-b", {"symbol": "BTCUSDT", "quantity": 0.002})
    with pytest.raises(IntentInFlightError):
        oracle_store.reserve(client_id, "payload-a", original)

    oracle_store.transition(client_id, "UNKNOWN", error="demo response timed out")
    with pytest.raises(IntentInFlightError):
        oracle_store.reserve(client_id, "payload-a", original)


def test_oracle_ledger_audit_events_are_readable_and_ordered(oracle_store):
    client_id = f"ora-{uuid.uuid4().hex}"
    oracle_store.reserve(client_id, "hash", {"symbol": "ETHUSDT"})
    oracle_store.transition(client_id, "SUBMITTING")
    oracle_store.transition(client_id, "FILLED", result={"status": "FILLED", "avg_price": 100.0})

    events = oracle_store.list_audit_events(client_order_id=client_id)
    assert [event["event_type"] for event in events] == [
        "INTENT_RESERVED",
        "ORDER_STATE_TRANSITION",
        "ORDER_STATE_TRANSITION",
    ]
    assert events[-1]["details"]["result"] == {"status": "FILLED", "avg_price": 100.0}
    assert all(isinstance(event["event_id"], int) for event in events)


def test_oracle_ledger_lists_reconcilable_and_unresolved(oracle_store):
    first = f"ora-{uuid.uuid4().hex}"
    second = f"ora-{uuid.uuid4().hex}"
    third = f"ora-{uuid.uuid4().hex}"
    oracle_store.reserve(first, "hash-1", {"symbol": "BTCUSDT"})
    oracle_store.reserve(second, "hash-2", {"symbol": "ETHUSDT"})
    oracle_store.reserve(third, "hash-3", {"symbol": "SOLUSDT"})
    oracle_store.transition(second, "ACCEPTED", result={"status": "ACCEPTED"})
    oracle_store.transition(third, "REJECTED", error="risk gate rejected")

    unresolved = {row["client_order_id"] for row in oracle_store.list_unresolved(limit=100)}
    assert first in unresolved
    assert second not in unresolved
    assert third not in unresolved

    reconcilable = {row["client_order_id"] for row in oracle_store.list_reconcilable(limit=100)}
    assert first in reconcilable
    assert second in reconcilable
    assert third not in reconcilable
    assert len(oracle_store.list_reconcilable(limit=5000)) <= 1000


def test_oracle_portfolio_baseline_and_high_water(oracle_risk_store):
    account_key = f"ora-account-{uuid.uuid4().hex}"
    timestamp = 1_800_000_000.0

    first = oracle_risk_store.observe(
        account_key=account_key, reporting_currency="USD", equity=1000.0, timestamp=timestamp
    )
    second = oracle_risk_store.observe(
        account_key=account_key, reporting_currency="USD", equity=950.0, timestamp=timestamp + 60
    )
    third = oracle_risk_store.observe(
        account_key=account_key, reporting_currency="USD", equity=1020.0, timestamp=timestamp + 120
    )

    assert first["day_start_equity"] == 1000.0
    assert second["day_start_equity"] == 1000.0
    assert second["high_water_equity"] == 1000.0
    assert third["day_start_equity"] == 1000.0
    assert third["high_water_equity"] == 1020.0

    baseline = oracle_risk_store.get_baseline(
        account_key=account_key, reporting_currency="USD", utc_day=first["utc_day"]
    )
    assert baseline is not None
    assert baseline["day_start_equity"] == 1000.0
    assert baseline["high_water_equity"] == 1020.0
