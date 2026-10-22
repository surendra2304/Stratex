"""Execution intent idempotency guard and order deduplication.

Persistence contract (fail closed):

* The intent store is read and rewritten under :func:`atomic_io.locked_path`
  (thread + inter-process lock) with an atomic replace, so two processes can
  never both record the same intent or lose each other's records.
* A store that exists but cannot be read (invalid JSON, wrong shape, NaN) is
  **not** treated as empty — that would erase every recorded intent and
  re-open the door to duplicate orders. Instead the guard reports every intent
  as already seen and refuses new records until an operator repairs or moves
  the file (it is left in place untouched as evidence).
* Unreadable ledger lines are skipped one by one instead of aborting the scan,
  so a single torn line can no longer hide later intents.
"""

from __future__ import annotations

import json
import logging
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from atomic_io import atomic_write_json, load_json_state, locked_path

logger = logging.getLogger("stratex_quantdinger.idempotency")


class IntentStoreCorrupt(RuntimeError):
    """The intent store exists but is unreadable; new intents are refused."""


class IdempotencyGuard:
    def __init__(self, path: str | None = None, ledger_file: str | None = None):
        self.path = Path(path or os.getenv("EXECUTION_INTENTS_FILE", "execution_intents.json"))
        self.ledger_file = Path(ledger_file) if ledger_file else None
        self._lock = threading.Lock()
        self.last_error: str | None = None

    def _load(self) -> dict[str, Any]:
        """Return the intent map; raise :class:`IntentStoreCorrupt` if it cannot be trusted."""
        result = load_json_state(self.path, expected_type=dict, quarantine=False)
        if result.status == "missing":
            return {}
        if result.status != "ok":
            self.last_error = result.error
            logger.critical(
                "Execution intent store %s is unreadable (%s); refusing new intents until it is repaired.",
                self.path, result.error,
            )
            raise IntentStoreCorrupt(f"{self.path}: {result.error}")
        bad = [key for key, value in result.data.items() if not isinstance(value, dict)]
        if bad:
            self.last_error = f"{len(bad)} malformed intent record(s)"
            logger.critical("Execution intent store %s holds malformed records %s", self.path, bad[:5])
            raise IntentStoreCorrupt(f"{self.path}: malformed records {bad[:5]}")
        return result.data

    def _save(self, data: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_json(self.path, data, indent=2, sort_keys=True, default=str)

    def _ledger_has(self, intent_id: str) -> bool:
        if not self.ledger_file or not self.ledger_file.exists():
            return False
        skipped = 0
        try:
            with open(self.ledger_file, "r", encoding="utf-8", errors="replace") as f:
                for line in f:
                    if not line.strip():
                        continue
                    try:
                        rec = json.loads(line)
                    except ValueError:
                        skipped += 1
                        continue
                    if isinstance(rec, dict) and (rec.get("signal_id") == intent_id or rec.get("intent_id") == intent_id):
                        return True
        except OSError as exc:
            # Cannot prove the intent is new: fail closed.
            logger.error("Ledger %s unreadable while checking intent %s: %s", self.ledger_file, intent_id, exc)
            return True
        if skipped:
            logger.warning("Skipped %d unreadable line(s) in %s while checking intents", skipped, self.ledger_file)
        return False

    def seen(self, intent_id: str) -> bool:
        """Checks if an intent has already been processed in the local intent store or ledger.

        Returns True (block) when the store is unreadable: an intent that cannot
        be proven new must not be executed.
        """
        with self._lock, locked_path(self.path):
            try:
                data = self._load()
            except IntentStoreCorrupt:
                return True
            if intent_id in data:
                return True
            return self._ledger_has(intent_id)

    def record(
        self,
        intent_id: str,
        exchange_order_id: str | None = None,
        status: str = "SUBMITTED",
        metadata: dict[str, Any] | None = None,
    ) -> bool:
        """Atomically records an intent. Returns False if already seen (or the
        store is unreadable / unwritable) to prevent duplicate orders."""
        with self._lock, locked_path(self.path):
            try:
                data = self._load()
            except IntentStoreCorrupt:
                return False
            if intent_id in data:
                return False

            now = datetime.now(timezone.utc).isoformat()
            data[intent_id] = {
                "intent_id": intent_id,
                "exchange_order_id": exchange_order_id,
                "status": status,
                "recorded_at": now,
                "metadata": metadata or {},
            }
            try:
                self._save(data)
            except (OSError, TypeError, ValueError) as exc:
                self.last_error = str(exc)
                logger.critical("Could not persist execution intent %s to %s: %s", intent_id, self.path, exc)
                return False
            return True

    def get_intent(self, intent_id: str) -> dict[str, Any] | None:
        with self._lock, locked_path(self.path):
            try:
                data = self._load()
            except IntentStoreCorrupt:
                return None
            return data.get(intent_id)

    def list_intents(self, limit: int = 50) -> list[dict[str, Any]]:
        with self._lock, locked_path(self.path):
            try:
                data = self._load()
            except IntentStoreCorrupt:
                return []
            items = list(data.values())
            items.sort(key=lambda x: str(x.get("recorded_at", "")), reverse=True)
            return items[:max(0, int(limit))]
