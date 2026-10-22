"""Durability and market-source tests for the paper-only shadow scheduler."""

import ast
from pathlib import Path

import pandas as pd
import pytest

import paper_shadow_processor as processor_module
from paper_shadow_memora import (
    ShadowPersistenceError,
    _restore_processor,
    _snapshot_processor,
)
from paper_shadow_scheduler import (
    EVIDENCE_STATUS,
    PUBLIC_DATA_SOURCE,
    ShadowPaperScheduler,
)


def make_klines(count=3, *, now_offset_ms=0):
    interval = 4 * 60 * 60 * 1000
    start = int(pd.Timestamp("2026-01-01T00:00:00Z").timestamp() * 1000)
    rows = []
    for index in range(count):
        opened = start + index * interval
        closed = opened + interval - 1
        rows.append([
            opened, "100", "102", "99", str(100 + index), "10", closed,
            "1000", 10, "5", "500", "0",
        ])
    return rows, rows[-1][6] + now_offset_ms


class FakeMemora:
    def __init__(self):
        self.save_calls = 0

    def load_latest(self, _processor):
        return False

    def save(self, _processor):
        self.save_calls += 1
        return {"storage_durable": True, "memory_id": f"receipt-{self.save_calls}"}


class ReadOnlyPublicClient:
    public_data_source = PUBLIC_DATA_SOURCE

    def __init__(self, rows):
        self.rows = rows
        self.calls = []

    def get_public_futures_klines(self, **kwargs):
        self.calls.append(kwargs)
        return self.rows

    def __getattr__(self, name):
        if "order" in name.lower() or "account" in name.lower():
            raise AssertionError(f"paper shadow must never access {name}")
        raise AttributeError(name)


def test_only_closed_public_candle_is_checkpointed_once(monkeypatch):
    rows, now_ms = make_klines(now_offset_ms=-1)
    client = ReadOnlyPublicClient(rows)
    store = FakeMemora()
    monkeypatch.setattr(
        processor_module, "_add_strategy_features", lambda _name, frame: frame
    )
    monkeypatch.setattr(
        processor_module,
        "_get_signal",
        lambda *_args: type("NoSignal", (), {"side": None, "sl": None, "tp": None})(),
    )
    scheduler = ShadowPaperScheduler(
        store=store,
        market_data_client=client,
        streams=(("adx_ema", "BTCUSDT", "4h"),),
        now_ms=lambda: now_ms,
        enabled=True,
    )

    assert scheduler.process_once() is True
    cursor = scheduler.processor.last_timestamps[("adx_ema", "BTCUSDT", "4h")]
    # The newest raw kline is still forming; the cursor points to the prior one.
    assert cursor == pd.Timestamp(rows[-2][0], unit="ms", tz="UTC")
    assert scheduler.get_status()["shadow_paper_data_source"] == "BINANCE_PUBLIC"
    assert scheduler.get_status()["shadow_paper_evidence_status"] == EVIDENCE_STATUS
    saves_after_first_cycle = store.save_calls

    assert scheduler.process_once() is False
    assert scheduler.processor.last_timestamps[("adx_ema", "BTCUSDT", "4h")] == cursor
    assert store.save_calls == saves_after_first_cycle
    assert len(client.calls) == 2
    assert all(call["symbol"] == "BTCUSDT" for call in client.calls)
    assert scheduler.get_status()["shadow_paper_status"] == "RUNNING"


def test_memora_unavailable_prevents_market_access_and_all_state_transitions():
    class UnavailableMemora:
        def load_latest(self, _processor):
            return False

        def save(self, _processor):
            raise ShadowPersistenceError("storage not durable")

    rows, now_ms = make_klines(now_offset_ms=2)
    client = ReadOnlyPublicClient(rows)
    scheduler = ShadowPaperScheduler(
        store=UnavailableMemora(),
        market_data_client=client,
        streams=(("adx_ema", "BTCUSDT", "4h"),),
        now_ms=lambda: now_ms,
        enabled=True,
    )

    assert scheduler.process_once() is False
    status = scheduler.get_status()
    assert status["shadow_paper_status"] == "DEGRADED"
    assert status["shadow_paper_ready"] is False
    assert "memora_bootstrap_write_failed" in status["shadow_paper_reason"]
    assert not client.calls
    assert not scheduler.processor.last_timestamps
    assert scheduler.process_once() is False  # retries the durable preflight
    assert not client.calls


def test_memora_bootstrap_reports_safe_adapter_failure_detail():
    class UnauthorizedMemora:
        def load_latest(self, _processor):
            raise ShadowPersistenceError("Memora returned HTTP 401")

        def save(self, _processor):
            raise AssertionError("save must not happen after failed restore")

    scheduler = ShadowPaperScheduler(store=UnauthorizedMemora(), enabled=True)

    assert scheduler.process_once() is False
    reason = scheduler.get_status()["shadow_paper_reason"]
    assert reason == "memora_restore_failed:Memora returned HTTP 401"
    assert "Bearer" not in reason
    assert not scheduler.processor.last_timestamps
    assert scheduler.process_once() is False  # retries without exposing credentials


def test_non_public_or_testnet_candle_client_is_rejected_before_use():
    class TestnetOnlyClient(ReadOnlyPublicClient):
        public_data_source = "BINANCE_TESTNET_READ_ONLY"

        def get_public_futures_klines(self, **kwargs):
            raise AssertionError("testnet data must never be accepted as public production data")

    rows, now_ms = make_klines(now_offset_ms=2)
    client = TestnetOnlyClient(rows)
    scheduler = ShadowPaperScheduler(
        store=FakeMemora(),
        market_data_client=client,
        streams=(("adx_ema", "BTCUSDT", "4h"),),
        now_ms=lambda: now_ms,
        enabled=True,
    )

    assert scheduler.process_once() is False
    assert scheduler.get_status()["shadow_paper_status"] == "DEGRADED"
    assert "market_data:RuntimeError" in scheduler.get_status()["shadow_paper_reason"]
    assert not scheduler.processor.last_timestamps


def test_checkpoint_failure_discards_trial_cursor_and_retries_without_restart(monkeypatch):
    class FailAfterBootstrap(FakeMemora):
        failed_checkpoint = False

        def save(self, processor):
            self.save_calls += 1
            if self.save_calls > 1 and not self.failed_checkpoint:
                self.failed_checkpoint = True
                raise ShadowPersistenceError("checkpoint unavailable")
            return {"storage_durable": True, "memory_id": "bootstrap"}

    rows, now_ms = make_klines(now_offset_ms=2)
    client = ReadOnlyPublicClient(rows)
    store = FailAfterBootstrap()
    monkeypatch.setattr(
        processor_module, "_add_strategy_features", lambda _name, frame: frame
    )
    monkeypatch.setattr(
        processor_module,
        "_get_signal",
        lambda *_args: type("NoSignal", (), {"side": None, "sl": None, "tp": None})(),
    )
    scheduler = ShadowPaperScheduler(
        store=store,
        market_data_client=client,
        streams=(("adx_ema", "BTCUSDT", "4h"),),
        now_ms=lambda: now_ms,
        enabled=True,
    )

    assert scheduler.process_once() is False
    assert scheduler.stopped is False
    assert not scheduler.processor.last_timestamps  # trial state was never adopted
    assert scheduler.get_status()["shadow_paper_status"] == "DEGRADED"
    assert "memora_checkpoint_failed" in scheduler.get_status()["shadow_paper_reason"]

    # Durable storage recovers without restarting the service. The old cursor
    # is reloaded and the same candle can be safely retried.
    assert scheduler.process_once() is True
    assert scheduler.get_status()["shadow_paper_status"] == "RUNNING"
    assert scheduler.processor.last_timestamps


def test_scheduler_has_no_exchange_execution_imports_or_order_calls():
    source = (Path(__file__).resolve().parents[1] / "paper_shadow_scheduler.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    imported_modules = {
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
    }
    assert not any(module and module.split(".")[0] == "execution" for module in imported_modules)
    assert "place_market_order" not in source
    assert "create_order" not in source


def test_enabled_by_default_and_false_environment_is_a_kill_switch(monkeypatch):
    monkeypatch.delenv("STRATEX_SHADOW_PAPER_ENABLED", raising=False)
    assert ShadowPaperScheduler(store=FakeMemora()).enabled is True
    monkeypatch.setenv("STRATEX_SHADOW_PAPER_ENABLED", "false")
    disabled = ShadowPaperScheduler(store=FakeMemora())
    assert disabled.enabled is False
    assert disabled.process_once() is False
    assert disabled.get_status()["shadow_paper_status"] == "DISABLED"


def test_memora_checkpoint_records_and_validates_public_market_source():
    processor = processor_module.ShadowPaperProcessor()
    processor.market_data_source = PUBLIC_DATA_SOURCE
    checkpoint = _snapshot_processor(processor)
    assert checkpoint["market_data_source"] == "BINANCE_PUBLIC"

    restored = processor_module.ShadowPaperProcessor()
    restored.market_data_source = PUBLIC_DATA_SOURCE
    _restore_processor(restored, checkpoint)
    assert restored.market_data_source == "BINANCE_PUBLIC"

    wrong_source = processor_module.ShadowPaperProcessor()
    wrong_source.market_data_source = "BINANCE_TESTNET_READ_ONLY"
    with pytest.raises(ShadowPersistenceError, match="market data source differs"):
        _restore_processor(wrong_source, checkpoint)
