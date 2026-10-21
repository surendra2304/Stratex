"""Bounded asynchronous webhook dispatch with HMAC signatures and retries.

Delivery is best-effort: failed delivery is stored in a bounded in-memory dead
letter queue and queue saturation is reported rather than spawning unlimited
threads or growing memory without limit.
"""

from __future__ import annotations

import copy
import json
import math
import os
import queue
import threading
import time
from collections import deque
from numbers import Integral, Real
from typing import Any

import requests

from logger import get_logger
from security_hardening import sign_audit_record

logger = get_logger("webhooks")


class EcosystemWebhookEmitter:
    """Dispatch webhook events through a fixed-size daemon worker pool."""

    SUCCESS_CODES = frozenset({200, 201, 202, 204})

    def __init__(
        self,
        *,
        max_workers: int = 4,
        max_queue_size: int = 256,
        max_dead_letters: int = 1000,
        request_timeout: float = 4.0,
        backoff_base_seconds: float = 0.5,
        max_backoff_seconds: float = 30.0,
    ) -> None:
        urls_str = os.getenv("WEBHOOK_URLS", "")
        self.webhook_urls = [u.strip() for u in urls_str.split(",") if u.strip()]

        for name, value in (
            ("max_workers", max_workers),
            ("max_queue_size", max_queue_size),
            ("max_dead_letters", max_dead_letters),
        ):
            if isinstance(value, bool) or not isinstance(value, Integral) or value < 1:
                raise ValueError(f"{name} must be a positive integer")

        normalized_timing: dict[str, float] = {}
        for name, value in (
            ("request_timeout", request_timeout),
            ("backoff_base_seconds", backoff_base_seconds),
            ("max_backoff_seconds", max_backoff_seconds),
        ):
            if isinstance(value, bool) or not isinstance(value, Real):
                raise ValueError(f"{name} must be a finite non-negative number")
            try:
                number = float(value)
            except (TypeError, ValueError, OverflowError) as exc:
                raise ValueError(f"{name} must be a finite non-negative number") from exc
            if not math.isfinite(number) or number < 0.0:
                raise ValueError(f"{name} must be a finite non-negative number")
            normalized_timing[name] = number

        if normalized_timing["request_timeout"] <= 0.0:
            raise ValueError("request_timeout must be a finite positive number")
        if normalized_timing["max_backoff_seconds"] < normalized_timing["backoff_base_seconds"]:
            raise ValueError("max_backoff_seconds must be >= backoff_base_seconds")

        self.max_workers = int(max_workers)
        self.max_queue_size = int(max_queue_size)
        self.max_dead_letters = int(max_dead_letters)
        self.request_timeout = normalized_timing["request_timeout"]
        self.backoff_base_seconds = normalized_timing["backoff_base_seconds"]
        self.max_backoff_seconds = normalized_timing["max_backoff_seconds"]

        self._queue: queue.Queue[dict[str, Any]] = queue.Queue(maxsize=self.max_queue_size)
        self._dlq_lock = threading.RLock()
        self.dead_letter_queue: deque[dict[str, Any]] = deque(maxlen=self.max_dead_letters)
        self.dead_letter_dropped_count = 0
        self._shutdown = threading.Event()
        self._workers: list[threading.Thread] = []

        # Avoid idle threads when no webhook destinations are configured.
        if self.webhook_urls:
            for index in range(self.max_workers):
                worker = threading.Thread(
                    target=self._worker_loop,
                    name=f"stratex-webhook-{index + 1}",
                    daemon=True,
                )
                worker.start()
                self._workers.append(worker)

    @staticmethod
    def _validate_event(event_type: str, data: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(event_type, str) or not event_type.strip():
            raise ValueError("event_type must be a non-empty string")
        if not isinstance(data, dict):
            raise ValueError("data must be a dictionary")
        try:
            # JSON round-trip validates serializability and detaches the queued
            # data from mutable caller-owned objects.
            detached = json.loads(json.dumps(data, allow_nan=False))
        except (TypeError, ValueError, OverflowError) as exc:
            raise ValueError("webhook data must be finite JSON-serializable data") from exc
        return {"event": event_type.strip(), "data": detached}

    def emit_event(self, event_type: str, data: dict[str, Any]) -> None:
        """Queue an event without creating a per-event thread or doing network I/O."""
        event = self._validate_event(event_type, data)
        if not self.webhook_urls:
            return

        payload: dict[str, Any] = {
            **event,
            "timestamp": time.time(),
            "source": "trading_bot",
        }
        try:
            payload["signature"] = sign_audit_record(payload)
        except (TypeError, ValueError, OverflowError) as exc:
            self._record_dead_letter(
                url="*",
                payload=payload,
                reason=f"SIGNING_FAILED:{type(exc).__name__}",
            )
            logger.exception("[WEBHOOK] Could not sign event %s", event_type)
            return

        if self._shutdown.is_set():
            self._record_dead_letter(url="*", payload=payload, reason="DISPATCHER_SHUTDOWN")
            return

        try:
            self._queue.put_nowait(payload)
        except queue.Full:
            self._record_dead_letter(url="*", payload=payload, reason="DISPATCH_QUEUE_FULL")
            logger.warning("[WEBHOOK] Queue full; rejected newest event %s", event_type)

    def _worker_loop(self) -> None:
        while not self._shutdown.is_set() or not self._queue.empty():
            try:
                payload = self._queue.get(timeout=0.25)
            except queue.Empty:
                continue
            try:
                self._dispatch_with_retry(payload)
            except Exception as exc:
                # One malformed event must not kill a worker and strand the queue.
                logger.exception("[WEBHOOK] Unexpected dispatch failure")
                self._record_dead_letter(
                    url="*",
                    payload=payload,
                    reason=f"DISPATCH_FAILED:{type(exc).__name__}",
                )
            finally:
                self._queue.task_done()

    def _record_dead_letter(
        self,
        *,
        url: str,
        payload: dict[str, Any],
        reason: str,
    ) -> None:
        entry = {
            "url": url,
            "payload": copy.deepcopy(payload),
            "failed_at": time.time(),
            "reason": str(reason)[:300],
        }
        with self._dlq_lock:
            if len(self.dead_letter_queue) >= self.max_dead_letters:
                self.dead_letter_dropped_count += 1
            self.dead_letter_queue.append(entry)

    def snapshot_dead_letters(self) -> list[dict[str, Any]]:
        """Return a defensive snapshot of the bounded dead-letter queue."""
        with self._dlq_lock:
            return copy.deepcopy(list(self.dead_letter_queue))

    def drain_dead_letters(self, limit: int | None = None) -> list[dict[str, Any]]:
        """Remove and return the oldest dead-letter entries."""
        if limit is not None and (
            isinstance(limit, bool) or not isinstance(limit, Integral) or limit < 1
        ):
            raise ValueError("limit must be a positive integer or None")
        with self._dlq_lock:
            count = len(self.dead_letter_queue) if limit is None else min(int(limit), len(self.dead_letter_queue))
            return [self.dead_letter_queue.popleft() for _ in range(count)]

    def wait_for_idle(self, timeout: float = 5.0) -> bool:
        """Wait until current queued work has finished, bounded by timeout."""
        if isinstance(timeout, bool) or not isinstance(timeout, Real):
            raise ValueError("timeout must be a finite non-negative number")
        timeout_value = float(timeout)
        if not math.isfinite(timeout_value) or timeout_value < 0.0:
            raise ValueError("timeout must be a finite non-negative number")
        deadline = time.monotonic() + timeout_value
        while self._queue.unfinished_tasks:
            remaining = deadline - time.monotonic()
            if remaining <= 0.0:
                return False
            time.sleep(min(0.01, remaining))
        return True

    def shutdown(self, *, wait: bool = True, timeout: float = 2.0) -> None:
        """Stop accepting new events; optionally wait for workers to drain queued work."""
        if not isinstance(wait, bool):
            raise ValueError("wait must be a boolean")
        if isinstance(timeout, bool) or not isinstance(timeout, Real):
            raise ValueError("timeout must be a finite non-negative number")
        timeout_value = float(timeout)
        if not math.isfinite(timeout_value) or timeout_value < 0.0:
            raise ValueError("timeout must be a finite non-negative number")
        self._shutdown.set()
        if wait:
            deadline = time.monotonic() + timeout_value
            for worker in self._workers:
                worker.join(timeout=max(0.0, deadline - time.monotonic()))

    def _dispatch_with_retry(self, payload: dict[str, Any], max_retries: int = 3) -> None:
        """Deliver to each endpoint and dead-letter persistent HTTP or network failures."""
        if isinstance(max_retries, bool) or not isinstance(max_retries, Integral) or max_retries < 1:
            raise ValueError("max_retries must be a positive integer")

        headers = {
            "Content-Type": "application/json",
            "X-Bot-Signature": payload.get("signature", ""),
        }

        for url in self.webhook_urls:
            success = False
            failure_reason = "NO_ATTEMPT"
            for attempt in range(int(max_retries)):
                try:
                    response = requests.post(
                        url,
                        json=payload,
                        headers=headers,
                        timeout=self.request_timeout,
                    )
                    if response.status_code in self.SUCCESS_CODES:
                        success = True
                        break
                    failure_reason = f"HTTP_STATUS_{response.status_code}"
                except requests.RequestException as exc:
                    failure_reason = f"REQUEST_ERROR:{type(exc).__name__}"

                # HTTP errors and network exceptions both receive bounded,
                # exponential backoff; the final attempt never sleeps.
                if attempt + 1 < max_retries:
                    delay = min(
                        self.backoff_base_seconds * (2 ** attempt),
                        self.max_backoff_seconds,
                    )
                    if delay > 0.0:
                        time.sleep(delay)

            if not success:
                logger.warning(
                    "[WEBHOOK] Failed to dispatch %s to %s after %s attempts (%s); stored in DLQ.",
                    payload.get("event", "UNKNOWN"),
                    url,
                    max_retries,
                    failure_reason,
                )
                self._record_dead_letter(
                    url=url,
                    payload=payload,
                    reason=failure_reason,
                )
