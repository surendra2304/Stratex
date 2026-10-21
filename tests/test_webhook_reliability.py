"""Reliability tests for bounded webhook dispatch and dead-letter handling."""

import threading

import pytest
import requests

import api.webhooks as webhook_module
from api.webhooks import EcosystemWebhookEmitter


@pytest.fixture
def configured_emitter(monkeypatch):
    monkeypatch.setenv("WEBHOOK_URLS", "https://one.example.test/hook")
    emitter = EcosystemWebhookEmitter(
        max_workers=1,
        max_queue_size=2,
        max_dead_letters=3,
        request_timeout=0.1,
        backoff_base_seconds=0.5,
        max_backoff_seconds=2.0,
    )
    yield emitter
    emitter.shutdown(wait=True, timeout=1.0)


def test_http_non_success_responses_receive_exponential_backoff(monkeypatch, configured_emitter):
    statuses = iter([503, 429, 200])
    sleeps = []
    monkeypatch.setattr(webhook_module.time, "sleep", sleeps.append)

    class Response:
        def __init__(self, status_code):
            self.status_code = status_code

    monkeypatch.setattr(
        webhook_module.requests,
        "post",
        lambda *args, **kwargs: Response(next(statuses)),
    )

    configured_emitter._dispatch_with_retry(
        {"event": "risk.limit_hit", "signature": "signed"},
        max_retries=3,
    )

    assert sleeps == [0.5, 1.0]
    assert configured_emitter.snapshot_dead_letters() == []


def test_network_exception_and_http_failures_dead_letter_with_reason(monkeypatch, configured_emitter):
    outcomes = iter([
        requests.Timeout("network timeout"),
        type("Response", (), {"status_code": 503})(),
        type("Response", (), {"status_code": 503})(),
    ])
    sleeps = []

    def fake_post(*args, **kwargs):
        result = next(outcomes)
        if isinstance(result, Exception):
            raise result
        return result

    monkeypatch.setattr(webhook_module.requests, "post", fake_post)
    monkeypatch.setattr(webhook_module.time, "sleep", sleeps.append)

    configured_emitter._dispatch_with_retry(
        {"event": "trade.closed", "signature": "signed"},
        max_retries=3,
    )

    assert sleeps == [0.5, 1.0]
    dead_letters = configured_emitter.snapshot_dead_letters()
    assert len(dead_letters) == 1
    assert dead_letters[0]["url"] == "https://one.example.test/hook"
    assert dead_letters[0]["reason"] == "HTTP_STATUS_503"


def test_dispatch_queue_is_bounded_and_rejects_newest_when_full(monkeypatch):
    monkeypatch.setenv("WEBHOOK_URLS", "https://one.example.test/hook")
    emitter = EcosystemWebhookEmitter(max_workers=1, max_queue_size=1, max_dead_letters=4)
    first_started = threading.Event()
    allow_first_to_finish = threading.Event()
    second_processed = threading.Event()
    counter_lock = threading.Lock()
    calls = 0

    def blocked_dispatch(payload):
        nonlocal calls
        with counter_lock:
            calls += 1
            call_number = calls
        if call_number == 1:
            first_started.set()
            allow_first_to_finish.wait(timeout=2.0)
        elif call_number == 2:
            second_processed.set()

    emitter._dispatch_with_retry = blocked_dispatch

    try:
        emitter.emit_event("event.first", {"n": 1})
        assert first_started.wait(timeout=1.0)
        emitter.emit_event("event.second", {"n": 2})
        emitter.emit_event("event.third", {"n": 3})

        records = emitter.snapshot_dead_letters()
        assert len(records) == 1
        assert records[0]["reason"] == "DISPATCH_QUEUE_FULL"
        assert records[0]["payload"]["event"] == "event.third"

        allow_first_to_finish.set()
        assert second_processed.wait(timeout=1.0)
        assert emitter.wait_for_idle(timeout=1.0)
    finally:
        allow_first_to_finish.set()
        emitter.shutdown(wait=True, timeout=1.0)


def test_dead_letter_queue_is_capped_and_can_be_drained(monkeypatch):
    monkeypatch.delenv("WEBHOOK_URLS", raising=False)
    emitter = EcosystemWebhookEmitter(max_dead_letters=2)

    for index in range(3):
        emitter._record_dead_letter(
            url=f"https://{index}.example.test/hook",
            payload={"event": "risk.limit_hit", "index": index},
            reason="HTTP_STATUS_503",
        )

    snapshot = emitter.snapshot_dead_letters()
    assert len(snapshot) == 2
    assert [entry["payload"]["index"] for entry in snapshot] == [1, 2]
    assert emitter.dead_letter_dropped_count == 1

    snapshot[0]["payload"]["index"] = 999
    assert emitter.snapshot_dead_letters()[0]["payload"]["index"] == 1
    drained = emitter.drain_dead_letters(limit=1)
    assert drained[0]["payload"]["index"] == 1
    assert len(emitter.snapshot_dead_letters()) == 1


@pytest.mark.parametrize(
    "kwargs",
    [
        {"max_workers": 0},
        {"max_queue_size": True},
        {"max_dead_letters": -1},
        {"request_timeout": 0},
        {"request_timeout": float("inf")},
        {"backoff_base_seconds": -0.1},
        {"max_backoff_seconds": 0.1, "backoff_base_seconds": 0.5},
    ],
)
def test_dispatcher_rejects_invalid_capacity_and_timing_configuration(monkeypatch, kwargs):
    monkeypatch.delenv("WEBHOOK_URLS", raising=False)
    with pytest.raises(ValueError):
        EcosystemWebhookEmitter(**kwargs)


@pytest.mark.parametrize(
    "event_type,data",
    [
        ("", {}),
        ("trade.closed", []),
        ("trade.closed", {"pnl": float("nan")}),
        ("trade.closed", {"price": float("inf")}),
        ("trade.closed", {"object": object()}),
    ],
)
def test_emit_event_rejects_invalid_or_non_json_data(monkeypatch, event_type, data):
    monkeypatch.delenv("WEBHOOK_URLS", raising=False)
    emitter = EcosystemWebhookEmitter()
    with pytest.raises(ValueError):
        emitter.emit_event(event_type, data)


def test_no_destination_means_no_worker_threads(monkeypatch):
    monkeypatch.delenv("WEBHOOK_URLS", raising=False)
    emitter = EcosystemWebhookEmitter()
    assert emitter._workers == []
    emitter.emit_event("trade.closed", {"symbol": "BTCUSDT"})
