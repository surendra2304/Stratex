"""Execution intent idempotency guard and order deduplication."""

from __future__ import annotations
import json
import threading
from pathlib import Path
from datetime import datetime, timezone
from typing import Any


class IdempotencyGuard:
    def __init__(self, path: str = "execution_intents.json", ledger_file: str | None = None):
        self.path = Path(path)
        self.ledger_file = Path(ledger_file) if ledger_file else None
        self._lock = threading.Lock()

    def _load(self) -> dict[str, Any]:
        if not self.path.exists():
            return {}
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except Exception:
            return {}

    def _save(self, data: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")
        tmp.replace(self.path)

    def seen(self, intent_id: str) -> bool:
        """Checks if an intent has already been processed in the local intent store or ledger."""
        with self._lock:
            data = self._load()
            if intent_id in data:
                return True

            # Also check existing trade ledger if configured
            if self.ledger_file and self.ledger_file.exists():
                try:
                    with open(self.ledger_file, "r", encoding="utf-8") as f:
                        for line in f:
                            if not line.strip():
                                continue
                            rec = json.loads(line)
                            if rec.get("signal_id") == intent_id or rec.get("intent_id") == intent_id:
                                return True
                except Exception:
                    pass

            return False

    def record(
        self,
        intent_id: str,
        exchange_order_id: str | None = None,
        status: str = "SUBMITTED",
        metadata: dict[str, Any] | None = None,
    ) -> bool:
        """Atomically records an intent. Returns False if already seen to prevent duplicate orders."""
        with self._lock:
            data = self._load()
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
            self._save(data)
            return True

    def get_intent(self, intent_id: str) -> dict[str, Any] | None:
        with self._lock:
            data = self._load()
            return data.get(intent_id)

    def list_intents(self, limit: int = 50) -> list[dict[str, Any]]:
        with self._lock:
            data = self._load()
            items = list(data.values())
            items.sort(key=lambda x: x.get("recorded_at", ""), reverse=True)
            return items[:limit]
