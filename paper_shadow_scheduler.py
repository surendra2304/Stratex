"""Opt-in live market-data scheduler for durable, paper-only shadow research.

This scheduler is separate from the frozen ``paper_forward_runner`` and never
imports the testnet execution service. It will not request candles until a
Memora checkpoint has been restored (if present) and a durable receipt for the
current state has been confirmed. A failed checkpoint discards trial state and
retries the durable preflight after the next poll interval.
"""

from __future__ import annotations

import copy
import datetime as dt
import os
import threading
import time
from collections import defaultdict
from typing import Any, Callable

import pandas as pd

from market_data_quality import UNUSABLE, sanitize_ohlcv
from paper_shadow_memora import MemoraShadowStore, ShadowPersistenceError
from paper_shadow_processor import (
    DEFAULT_SHADOW_STREAMS,
    EVIDENCE_STATUS,
    ShadowPaperProcessor,
)


PUBLIC_DATA_SOURCE = "BINANCE_PUBLIC"
POLL_SECONDS = 60
CANDLE_LIMIT = 500
TIMEFRAME_MS = {
    "1m": 60_000,
    "3m": 180_000,
    "5m": 300_000,
    "15m": 900_000,
    "30m": 1_800_000,
    "1h": 3_600_000,
    "2h": 7_200_000,
    "4h": 14_400_000,
    "6h": 21_600_000,
    "8h": 28_800_000,
    "12h": 43_200_000,
    "1d": 86_400_000,
}
ENABLED_ENV = "STRATEX_SHADOW_PAPER_ENABLED"


class ShadowPaperScheduler:
    """Durability-gated processor for real, completed public klines."""

    def __init__(
        self,
        *,
        store: Any | None = None,
        market_data_client: Any | None = None,
        market_data_factory: Callable[[], Any] | None = None,
        processor: ShadowPaperProcessor | None = None,
        streams: tuple[tuple[str, str, str], ...] = DEFAULT_SHADOW_STREAMS,
        now_ms: Callable[[], float] | None = None,
        poll_seconds: int = POLL_SECONDS,
        enabled: bool | None = None,
    ) -> None:
        self.store = store or MemoraShadowStore()
        self.market_data_client = market_data_client
        self.market_data_factory = market_data_factory or _create_read_only_market_data_client
        self.processor = processor or ShadowPaperProcessor()
        self.processor.market_data_source = PUBLIC_DATA_SOURCE
        self.streams = tuple(streams)
        self.now_ms = now_ms or (lambda: time.time() * 1000)
        self.poll_seconds = max(1, int(poll_seconds))
        env_enabled = os.getenv(ENABLED_ENV, "true").strip().lower() not in {"0", "false", "no", "off"}
        self.enabled = env_enabled if enabled is None else bool(enabled)
        self.ready = False
        self.stopped = False
        self.status: dict[str, Any] = {
            "shadow_paper_status": "STARTING" if self.enabled else "DISABLED",
            "shadow_paper_enabled": self.enabled,
            "shadow_paper_evidence_status": EVIDENCE_STATUS,
            "shadow_paper_data_source": "NOT_CONNECTED",
            "shadow_paper_reason": "awaiting_memora_durable_receipt" if self.enabled else "disabled_by_environment",
            "shadow_paper_last_checkpoint_at": None,
            "shadow_paper_last_checkpoint_id": None,
            "shadow_paper_candles_processed": 0,
            "shadow_paper_stream_count": len(self.streams),
            "shadow_paper_last_cycle_at": None,
        }
        self._status_lock = threading.Lock()
        self._validate_streams()

    def _validate_streams(self) -> None:
        if not self.streams:
            raise ValueError("at least one paper shadow stream is required")
        if len(set(self.streams)) != len(self.streams):
            raise ValueError("paper shadow streams must be unique")
        from paper_shadow_processor import SHADOW_CANDIDATES

        for strategy, _symbol, timeframe in self.streams:
            if strategy not in SHADOW_CANDIDATES:
                raise ValueError(f"unsupported observe-only shadow candidate: {strategy}")
            if timeframe not in TIMEFRAME_MS:
                raise ValueError(f"unsupported shadow timeframe: {timeframe}")

    def _set_status(self, status: str, reason: str | None = None, **fields: Any) -> None:
        with self._status_lock:
            self.status["shadow_paper_status"] = status
            if reason is not None:
                self.status["shadow_paper_reason"] = reason
            self.status.update(fields)

    def get_status(self) -> dict[str, Any]:
        with self._status_lock:
            return dict(self.status)

    def _bootstrap(self) -> bool:
        if not self.enabled:
            self._set_status("DISABLED", "disabled_by_environment")
            self.stopped = True
            return False
        try:
            restored = self.store.load_latest(self.processor)
        except Exception as exc:
            detail = _safe_persistence_detail(exc)
            self._set_status(
                "DEGRADED",
                f"memora_restore_failed:{detail}",
                shadow_paper_ready=False,
            )
            return False
        try:
            receipt = self.store.save(self.processor)
            _require_durable_receipt(receipt)
        except Exception as exc:
            detail = _safe_persistence_detail(exc)
            self._set_status(
                "DEGRADED",
                f"memora_bootstrap_write_failed:{detail}",
                shadow_paper_ready=False,
            )
            return False
        self.ready = True
        self._set_status(
            "RUNNING",
            "memora_durable_receipt_confirmed",
            shadow_paper_ready=True,
            shadow_paper_state_restored=bool(restored),
            shadow_paper_last_checkpoint_at=dt.datetime.now(dt.timezone.utc).isoformat(),
            shadow_paper_last_checkpoint_id=receipt.get("memory_id"),
        )
        return True

    def process_once(self) -> bool:
        """Poll real klines and commit a batch only after durable Memora receipt.

        Returns True when at least one candle transition was durably committed.
        Data failures are reported as degraded and retried on a later cycle;
        Memora failures stop this scheduler instance and discard the trial copy.
        """
        if self.stopped:
            return False
        if not self.ready and not self._bootstrap():
            return False

        trial = copy.deepcopy(self.processor)
        grouped: dict[tuple[str, str], list[tuple[str, str, str]]] = defaultdict(list)
        for stream in self.streams:
            grouped[(stream[1], stream[2])].append(stream)

        errors: list[str] = []
        transitions = 0
        client = None
        for (symbol, timeframe), stream_keys in grouped.items():
            try:
                if client is None:
                    client = self.market_data_client or self.market_data_factory()
                    self.market_data_client = client
                candles = _fetch_closed_candles(
                    client,
                    symbol,
                    timeframe,
                    limit=CANDLE_LIMIT,
                    now_ms=self.now_ms(),
                )
                self._set_status("STARTING", "reading_public_klines", shadow_paper_data_source=PUBLIC_DATA_SOURCE)
            except Exception as exc:
                errors.append(f"{symbol}/{timeframe}:market_data:{type(exc).__name__}")
                continue
            if candles.empty:
                errors.append(f"{symbol}/{timeframe}:no_closed_candles")
                continue

            for strategy, stream_symbol, stream_timeframe in stream_keys:
                stream_key = (strategy, stream_symbol, stream_timeframe)
                cursor = trial.last_timestamps.get(stream_key)
                if cursor is None:
                    # Cold start: use the newest completed candle only. Earlier
                    # history is feature context, never counted as a sample.
                    indices = [len(candles) - 1]
                else:
                    newer = candles.index[candles["timestamp"] > cursor].tolist()
                    if not newer:
                        continue
                    expected = cursor + pd.Timedelta(milliseconds=TIMEFRAME_MS[timeframe])
                    first_new = candles.iloc[newer[0]]["timestamp"]
                    if first_new != expected:
                        errors.append(f"{strategy}/{symbol}/{timeframe}:candle_gap")
                        continue
                    indices = newer

                for index in indices:
                    decision = trial.process_closed_candle(
                        strategy,
                        stream_symbol,
                        stream_timeframe,
                        candles.iloc[: index + 1],
                    )
                    if decision["decision"] != "DUPLICATE_OR_OUT_OF_ORDER":
                        transitions += 1

        self._set_status(
            "RUNNING" if not errors else "DEGRADED",
            "market_data_ok" if not errors else ";".join(errors[:8]),
            shadow_paper_last_cycle_at=dt.datetime.now(dt.timezone.utc).isoformat(),
        )
        if transitions == 0:
            return False

        try:
            receipt = self.store.save(trial)
            _require_durable_receipt(receipt)
        except Exception as exc:
            detail = _safe_persistence_detail(exc)
            self._set_status(
                "DEGRADED",
                f"memora_checkpoint_failed:{detail}",
                shadow_paper_ready=False,
                shadow_paper_uncommitted_transitions=transitions,
            )
            return False

        # Publish the in-memory state only after Memora confirms durability.
        self.processor = trial
        self._set_status(
            "RUNNING" if not errors else "DEGRADED",
            "memora_durable_receipt_confirmed" if not errors else ";".join(errors[:8]),
            shadow_paper_ready=True,
            shadow_paper_last_checkpoint_at=dt.datetime.now(dt.timezone.utc).isoformat(),
            shadow_paper_last_checkpoint_id=receipt.get("memory_id"),
            shadow_paper_candles_processed=(
                int(self.status.get("shadow_paper_candles_processed", 0)) + transitions
            ),
            shadow_paper_uncommitted_transitions=0,
        )
        return True

    def run_forever(self) -> None:
        """Poll until disabled or explicitly stopped, retrying transient failures."""
        if not self.enabled:
            self._bootstrap()
            return
        while not self.stopped:
            self.process_once()
            if not self.stopped:
                time.sleep(self.poll_seconds)


def _require_durable_receipt(receipt: Any) -> None:
    if not isinstance(receipt, dict) or receipt.get("storage_durable") is not True:
        raise ShadowPersistenceError("Memora durable checkpoint receipt was not confirmed")


def _safe_persistence_detail(exc: Exception) -> str:
    """Expose only adapter-authored persistence diagnostics, never raw errors."""
    if isinstance(exc, ShadowPersistenceError):
        return str(exc).replace("\n", " ")[:160] or type(exc).__name__
    return type(exc).__name__


def _create_read_only_market_data_client() -> Any:
    # Lazy import: failed Memora preflight does not even construct a market
    # client. MarketDataClient exposes read-only get_klines and no order API.
    from data_client import MarketDataClient

    client = MarketDataClient()
    if getattr(client, "public_data_source", None) != PUBLIC_DATA_SOURCE:
        raise RuntimeError("BINANCE_PUBLIC USD-M market data client unavailable")
    if not hasattr(client, "get_public_futures_klines"):
        raise RuntimeError("public USD-M read-only klines method unavailable")
    return client


def _fetch_closed_candles(
    client: Any,
    symbol: str,
    timeframe: str,
    *,
    limit: int,
    now_ms: float,
) -> pd.DataFrame:
    if getattr(client, "public_data_source", None) != PUBLIC_DATA_SOURCE:
        raise RuntimeError("market data source is not confirmed as BINANCE_PUBLIC")
    if not hasattr(client, "get_public_futures_klines"):
        raise RuntimeError("market data client lacks production public USD-M klines")
    raw = client.get_public_futures_klines(symbol=symbol, interval=timeframe, limit=limit)
    if not raw:
        return pd.DataFrame(columns=["timestamp", "open", "high", "low", "close", "volume"])
    columns = [
        "timestamp", "open", "high", "low", "close", "volume", "close_time",
        "quote_volume", "n_trades", "taker_buy_base", "taker_buy_quote", "ignore",
    ]
    frame = pd.DataFrame(raw, columns=columns)
    frame["close_time"] = pd.to_numeric(frame["close_time"], errors="coerce")
    frame = frame.loc[frame["close_time"].notna() & (frame["close_time"] < float(now_ms))].copy()
    if frame.empty:
        return pd.DataFrame(columns=["timestamp", "open", "high", "low", "close", "volume"])
    frame["timestamp"] = pd.to_datetime(pd.to_numeric(frame["timestamp"], errors="raise"), unit="ms", utc=True)
    for column in ("open", "high", "low", "close", "volume"):
        frame[column] = pd.to_numeric(frame[column], errors="raise")
    frame, quality = sanitize_ohlcv(frame[["timestamp", "open", "high", "low", "close", "volume"]], interval=timeframe)
    if quality.status == UNUSABLE:
        # The shadow sample must not evaluate an older bar as the newest one,
        # nor bars with NaN/zero prices or high<low.
        raise RuntimeError(f"unusable public candles for {symbol} {timeframe}: {quality.summary()}")
    return frame


_singleton: ShadowPaperScheduler | None = None
_singleton_lock = threading.Lock()
_scheduler_thread: threading.Thread | None = None
_global_status: dict[str, Any] = {
    "shadow_paper_status": "DISABLED",
    "shadow_paper_enabled": False,
    "shadow_paper_evidence_status": EVIDENCE_STATUS,
    "shadow_paper_data_source": "NOT_CONNECTED",
    "shadow_paper_reason": "not_started",
}


def start_shadow_paper_scheduler(*, force: bool = False) -> bool:
    """Start one background scheduler; enabled by default, killable by env."""
    global _singleton, _scheduler_thread, _global_status
    if os.getenv("PYTEST_CURRENT_TEST") or ("pytest" in __import__("sys").modules and not force):
        return False
    if os.getenv(ENABLED_ENV, "true").strip().lower() in {"0", "false", "no", "off"} and not force:
        _global_status = {
            "shadow_paper_status": "DISABLED",
            "shadow_paper_enabled": False,
            "shadow_paper_evidence_status": EVIDENCE_STATUS,
            "shadow_paper_reason": "disabled_by_environment",
        }
        return False
    with _singleton_lock:
        if _scheduler_thread is not None and _scheduler_thread.is_alive():
            return True
        _singleton = ShadowPaperScheduler()
        _global_status = _singleton.get_status()

        def run() -> None:
            global _global_status
            try:
                _singleton.run_forever()
            except BaseException as exc:
                _singleton.stopped = True
                _singleton._set_status("DEGRADED", f"scheduler_failed:{type(exc).__name__}")
            finally:
                _global_status = _singleton.get_status()

        _scheduler_thread = threading.Thread(
            target=run, name="stratex-paper-shadow", daemon=True
        )
        _scheduler_thread.start()
        return True


def get_shadow_scheduler_status() -> dict[str, Any]:
    if _singleton is not None:
        return _singleton.get_status()
    return dict(_global_status)
