"""Network-free tests for cloud-only paper-shadow checkpoint persistence."""

import json
from types import SimpleNamespace

import pandas as pd
import pytest

from paper_shadow_memora import MemoraShadowStore, NAMESPACE, ShadowPersistenceError


def processor():
    return SimpleNamespace(
        starting_capital=10_000.0,
        risk_fraction=0.0025,
        max_position_fraction=0.1,
        max_total_exposure_fraction=1.0,
        cost_engine=SimpleNamespace(
            entry_fee=0.001, exit_fee=0.001, entry_slip=0.0005,
            exit_slip=0.0005, spread=0.0001,
        ),
        realized_equity=9_990.0,
        positions={
            ("adx_ema", "BTCUSDT", "4h"): {
                "side": "LONG", "entry_price": 100.0, "quantity": 1.0,
                "entry_notional": 100.0, "stop": 98.0, "target": None,
                "signal_id": "sample-signal", "evidence_status": "UNVALIDATED_PAPER_SHADOW",
            }
        },
        pending_entries={},
        last_timestamps={
            ("adx_ema", "BTCUSDT", "4h"): pd.Timestamp("2026-01-01T00:00:00Z")
        },
        seen_signal_ids={"sample-signal"},
    )


def test_save_and_restore_uses_dedicated_memora_namespace_and_durable_receipt():
    calls = []
    stored = []

    def transport(method, url, headers, payload, timeout):
        calls.append((method, url, headers, payload, timeout))
        if method == "POST" and url.endswith("/v1/memories"):
            stored.append(payload)
            return 201, {"id": "memory-123", "storage_durable": True}
        if method == "POST" and url.endswith("/v1/memories/query"):
            return 200, [{"content_text": stored[-1]["content_text"]}]
        raise AssertionError("unexpected endpoint")

    store = MemoraShadowStore(api_key="test-stratex-key", transport=transport)
    original = processor()
    receipt = store.save(original)
    assert receipt == {
        "memory_id": "memory-123", "storage_durable": True, "namespace": NAMESPACE
    }
    write = calls[0]
    assert write[0] == "POST" and write[1].endswith("/v1/memories")
    assert write[2]["X-Agent-Name"] == "stratex"
    assert write[2]["Authorization"] == "Bearer test-stratex-key"
    assert write[3]["target_namespace_path"] == NAMESPACE
    assert write[3]["task_id"] == "paper-shadow-checkpoint"
    saved = json.loads(write[3]["content_text"])
    assert saved["kind"] == "stratex.paper_shadow.checkpoint"
    assert saved["positions"]["adx_ema|BTCUSDT|4h"]["side"] == "LONG"

    restored = processor()
    restored.realized_equity = 10_000.0
    restored.positions = {}
    restored.last_timestamps = {}
    restored.seen_signal_ids = set()
    assert store.load_latest(restored) is True
    assert restored.realized_equity == 9_990.0
    assert restored.positions == original.positions
    assert restored.last_timestamps == original.last_timestamps
    assert restored.seen_signal_ids == set()  # cursor timestamps prevent replay
    query = calls[-1][3]
    assert query["namespace_path"] == NAMESPACE
    assert query["task_id"] == "paper-shadow-checkpoint"


def test_missing_key_fails_closed_before_transport(monkeypatch):
    # The store resolves `self.api_key or os.getenv("STRATEX_API_KEY")`, and
    # config.py calls load_dotenv() at import time. This test therefore only
    # means "missing key" while the variable is genuinely absent -- previously it
    # passed only because no .env existed, and turned red as soon as a real key
    # was configured. Make the precondition explicit so the property is tested
    # rather than assumed.
    monkeypatch.delenv("STRATEX_API_KEY", raising=False)
    calls = []
    store = MemoraShadowStore(api_key="", transport=lambda *args: calls.append(args))
    with pytest.raises(ShadowPersistenceError, match="STRATEX_API_KEY"):
        store.save(processor())
    assert calls == []


def test_env_key_is_used_when_constructor_key_is_absent(monkeypatch):
    """Documented fallback: no constructor key means "use the environment".

    Guards the counterpart of the test above so tightening the missing-key path
    can never silently break the normal production wiring.
    """
    monkeypatch.setenv("STRATEX_API_KEY", "stratex_env_key_for_test")
    calls = []
    store = MemoraShadowStore(transport=lambda *args: calls.append(args) or (201, {
        "id": "memory-1", "storage_durable": True,
    }))
    assert store.save(processor())["memory_id"] == "memory-1"
    assert len(calls) == 1
    assert calls[0][2]["Authorization"] == "Bearer stratex_env_key_for_test"


def test_non_durable_receipt_fails_closed():
    store = MemoraShadowStore(
        api_key="test-key",
        transport=lambda *args: (201, {"id": "memory-123", "storage_durable": False}),
    )
    with pytest.raises(ShadowPersistenceError, match="durable"):
        store.save(processor())


def test_memora_outage_and_invalid_query_fail_closed():
    def outage(*_args):
        raise TimeoutError("network unavailable")

    with pytest.raises(ShadowPersistenceError, match="request failed"):
        MemoraShadowStore(api_key="test-key", transport=outage).save(processor())

    store = MemoraShadowStore(
        api_key="test-key", transport=lambda *_args: (200, {"status": "error"})
    )
    with pytest.raises(ShadowPersistenceError, match="invalid checkpoint query"):
        store.load_latest(processor())


def test_rate_limit_status_reports_only_allowlisted_origin_headers():
    safe_metadata = {
        "server": "cloudflare",
        "render_origin": "uvicorn",
        "render_request_id": "3d84049d-6172-4873",
        "cf_ray": "a4134d8cf9b2f088-DFW",
        "retry_after_seconds": "120",
        "authorization": "Bearer do-not-leak",
    }
    store = MemoraShadowStore(
        api_key="test-key",
        transport=lambda *_args: (429, {
            "_rate_limit_metadata": safe_metadata,
            "body": "sensitive provider details must not escape",
        }),
    )
    with pytest.raises(ShadowPersistenceError) as raised:
        store.load_latest(processor())
    message = str(raised.value)
    assert "HTTP 429" in message
    assert "server=cloudflare" in message
    assert "render_origin=uvicorn" in message
    assert "render_request_id=3d84049d-6172-4873" in message
    assert "cf_ray=a4134d8cf9b2f088-DFW" in message
    assert "retry_after_seconds=120" in message
    assert "do-not-leak" not in message
    assert "sensitive provider details" not in message


def test_rate_limit_metadata_rejects_untrusted_header_values():
    from email.message import Message

    from paper_shadow_memora import _safe_rate_limit_headers

    headers = Message()
    headers["Server"] = "attacker.example; Authorization=secret"
    headers["X-Render-Origin-Server"] = "uvicorn\nAuthorization: secret"
    headers["Rndr-Id"] = "secret-token"
    headers["CF-Ray"] = "not-a-ray"
    headers["Retry-After"] = "999999"
    assert _safe_rate_limit_headers(headers) == {}


def test_configuration_mismatch_and_foreign_snapshot_are_not_restored():
    state = json.dumps({"kind": "other-agent.checkpoint"})
    store = MemoraShadowStore(
        api_key="test-key",
        transport=lambda *_args: (200, [{"content_text": state}]),
    )
    assert store.load_latest(processor()) is False

    valid = processor()
    from paper_shadow_memora import _snapshot_processor

    state = _snapshot_processor(valid)
    state["configuration"]["risk_fraction"] = 0.005
    store = MemoraShadowStore(
        api_key="test-key",
        transport=lambda *_args: (200, [{"content_text": json.dumps(state)}]),
    )
    with pytest.raises(ShadowPersistenceError, match="configuration differs"):
        store.load_latest(processor())
