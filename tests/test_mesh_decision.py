"""Paper-only mesh decision tests: Stratex consumes Futuris advisories safely."""
from __future__ import annotations

import hashlib
import hmac
import json

import pytest

from autonomy.mesh_decision import (
    build_decision_envelope,
    consume_and_decide,
    evaluate_advisory,
)


@pytest.fixture
def advisory_payload():
    return {
        "source_agent": "futuris",
        "forecast_id": "forecast-1",
        "target": "intelx:sig-1",
        "status": "active",
        "prediction": 0.5,
        "range_lower": 0.2,
        "range_upper": 0.8,
        "probability": 0.7,
        "confidence": 0.84,
        "prediction_is_not_authorization": True,
        "correlation_id": "corr-journey-intelx-1",
    }


class _AllowAllPolicy:
    @staticmethod
    def can_place_order():
        return True, "ALLOWED_TESTNET"


def test_evaluate_advisory_stays_paper_only_even_when_policy_would_allow(advisory_payload):
    decision = evaluate_advisory(advisory_payload, execution_policy=_AllowAllPolicy)
    assert decision["paper_only"] is True
    assert decision["action"] == "watch"  # permitted elsewhere, but nothing placed here


def test_evaluate_advisory_records_real_policy_verdict(advisory_payload):
    from execution import ExecutionPolicy

    decision = evaluate_advisory(advisory_payload, execution_policy=ExecutionPolicy)
    assert decision["paper_only"] is True
    assert decision["action"] in {"paper_intent", "watch", "no_action"}
    assert decision["policy_reason"]
    assert decision["forecast_id"] == "forecast-1"


def test_live_mode_is_forbidden_by_design(advisory_payload, monkeypatch):
    import config
    import execution
    from execution import ExecutionPolicy

    monkeypatch.setattr(execution, "TRADING_MODE", "LIVE", raising=False)
    # The pinned safety truth table (tests/test_safety_gates.py) resolves
    # PAPER_SAFE_MODE before the LIVE branch: to observe the LIVE-specific
    # rejection reason both the execution-module binding and the config value
    # must be cleared. (The no-credentials config fallback sets PAPER_SAFE_MODE
    # to True in the test environment.)
    monkeypatch.setattr(execution, "PAPER_SAFE_MODE", False, raising=False)
    monkeypatch.setattr(config, "PAPER_SAFE_MODE", False, raising=False)
    allowed, reason = ExecutionPolicy.can_place_order()
    assert allowed is False
    assert reason == "LIVE_FORBIDDEN_BY_DESIGN"

    decision = evaluate_advisory(advisory_payload, execution_policy=ExecutionPolicy)
    assert decision["paper_only"] is True
    assert decision["action"] == "paper_intent"
    assert decision["policy_reason"] == "LIVE_FORBIDDEN_BY_DESIGN"


def test_decision_envelope_is_signed_and_threads_correlation(advisory_payload):
    decision = evaluate_advisory(advisory_payload, execution_policy=_AllowAllPolicy)
    decision["decision_id"] = "dec-1"
    envelope = build_decision_envelope(
        decision,
        correlation_id="corr-journey-intelx-1",
        signing_key="stratex-test-key",
        message_id="stratex-dec-1",
    )
    assert envelope["intent"] == "stratex.decision"
    assert envelope["from_agent"] == "stratex"
    assert envelope["to_agent"] == "all"
    assert envelope["correlation_id"] == "corr-journey-intelx-1"
    assert envelope["payload"]["paper_only"] is True
    signature = envelope.pop("signature")
    signed = json.dumps(envelope, sort_keys=True, separators=(",", ":"), default=str).encode()
    expected = hmac.new(b"stratex-test-key", signed, hashlib.sha256).hexdigest()
    assert hmac.compare_digest(signature, expected)


def test_build_decision_envelope_requires_signing_key(advisory_payload):
    decision = evaluate_advisory(advisory_payload, execution_policy=_AllowAllPolicy)
    with pytest.raises(RuntimeError, match="STRATEX_API_KEY"):
        build_decision_envelope(
            decision,
            correlation_id="corr",
            signing_key="",
            message_id="stratex-dec-2",
        )


class _StubFeed:
    """Durable-feed double with in-order ack tracking."""

    def __init__(self, events):
        self.events = events
        self.acked = []

    def read_event_cursor(self, agent, consumer_id):
        return {"status": "ok", "after_id": 0}

    def poll_events(self, agent, after_id, limit):
        return {"status": "ok", "events": self.events, "next_after_id": self.events[-1]["id"] if self.events else after_id, "has_more": False}

    def acknowledge_event(self, agent, event_id, consumer_id):
        self.acked.append(event_id)
        return {"status": "acknowledged"}


def test_consume_and_decide_publishes_and_acks_in_order(advisory_payload, monkeypatch):
    monkeypatch.setenv("STRATEX_API_KEY", "stratex-test-key")
    events = [
        {"id": 1, "event_id": "futuris-fc-1", "event_type": "intelx.news", "payload": {"headline": "other"}},
        {"id": 2, "event_id": "futuris-fc-2", "event_type": "futuris.forecast", "payload": advisory_payload},
    ]
    feed = _StubFeed(events)
    posted = {}

    def fake_post(base, envelope, key):
        posted["url"] = f"{base}/mesh/envelope"
        posted["envelope"] = envelope
        posted["key"] = key
        return {"status": "accepted", "event_id": envelope["message_id"], "cursor": 42}

    result = consume_and_decide(feed, post=fake_post)

    assert result["status"] == "ok"
    assert feed.acked == [1, 2]  # irrelevant event acked to keep cursor moving
    assert len(result["decisions"]) == 1
    record = result["decisions"][0]
    assert record["correlation_id"] == "corr-journey-intelx-1"
    assert record["action"] in {"paper_intent", "watch"}
    assert posted["envelope"]["payload"]["paper_only"] is True
    assert record["message_id"].startswith("stratex-dec-")
    assert posted["key"] == "stratex-test-key"
    assert posted["envelope"]["payload"]["forecast_id"] == "forecast-1"


def test_consume_and_decide_does_not_ack_when_publish_fails(advisory_payload, monkeypatch):
    monkeypatch.setenv("STRATEX_API_KEY", "stratex-test-key")
    events = [
        {"id": 7, "event_id": "futuris-fc-7", "event_type": "futuris.forecast", "payload": advisory_payload},
    ]
    feed = _StubFeed(events)

    def failing_post(base, envelope, key):
        return {"status": "failed_upstream", "status_code": 503}

    result = consume_and_decide(feed, post=failing_post)

    assert result["status"] == "error"
    assert result["stage"] == "publish"
    assert feed.acked == []  # cursor must not advance past unhandled advisory


def test_consume_and_decide_deduplicates_by_event_identity(advisory_payload, monkeypatch):
    monkeypatch.setenv("STRATEX_API_KEY", "stratex-test-key")
    events = [
        {"id": 3, "event_id": "futuris-fc-3", "event_type": "futuris.forecast", "payload": advisory_payload},
        {"id": 4, "event_id": "futuris-fc-3", "event_type": "futuris.forecast", "payload": advisory_payload},
    ]
    feed = _StubFeed(events)
    statuses = iter([{"status": "accepted", "event_id": "stratex-dec-x", "cursor": 9}, {"status": "duplicate", "event_id": "stratex-dec-x", "cursor": 9}])

    result = consume_and_decide(feed, post=lambda base, envelope, key: next(statuses))

    assert result["status"] == "ok"
    assert [d["publish_status"] for d in result["decisions"]] == ["accepted", "duplicate"]
    assert feed.acked == [3, 4]
