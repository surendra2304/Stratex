from __future__ import annotations

import threading
import time
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class IdempotencyEntry:
    key: str
    order_id: str
    created_at_ns: int


class IdempotencyStore:
    """Bounded in-memory idempotency store. Persist externally for multi-process deployments."""

    def __init__(self, ttl_seconds: float = 86400.0, max_entries: int = 100000):
        self.ttl_seconds = ttl_seconds
        self.max_entries = max_entries
        self._items: dict[str, IdempotencyEntry] = {}
        self._lock = threading.RLock()

    def _purge(self) -> None:
        cutoff = time.time_ns() - int(self.ttl_seconds * 1_000_000_000)
        stale = [k for k, v in self._items.items() if v.created_at_ns < cutoff]
        for k in stale:
            del self._items[k]
        if len(self._items) > self.max_entries:
            oldest = sorted(self._items.values(), key=lambda e: e.created_at_ns)
            for item in oldest[: len(self._items) - self.max_entries]:
                self._items.pop(item.key, None)

    def get(self, key: str) -> IdempotencyEntry | None:
        with self._lock:
            self._purge()
            return self._items.get(key)

    def put(self, key: str, order_id: str) -> IdempotencyEntry:
        with self._lock:
            self._purge()
            entry = IdempotencyEntry(key, order_id, time.time_ns())
            self._items[key] = entry
            return entry

    def check_or_reserve(self, key: str, order_id: str) -> tuple[bool, IdempotencyEntry]:
        with self._lock:
            self._purge()
            existing = self._items.get(key)
            if existing:
                return False, existing
            entry = self.put(key, order_id)
            return True, entry
