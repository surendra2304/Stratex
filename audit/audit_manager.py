"""
audit/audit_manager.py — Cryptographic audit trail & idempotency store.

Guarantees:
1. Append-only SHA-256 hash-chained audit logging for recommendations, rejections,
   authorizations, parameter applications, and panic events.
2. Idempotency management for order dispatch and parameter modifications.
"""

import datetime
import hashlib
import json
import os
import threading
from typing import Any

from logger import get_logger

logger = get_logger("audit_manager")

AUDIT_LOG_FILE = os.getenv("AUDIT_LOG_FILE", os.path.join(os.path.dirname(os.path.dirname(__file__)), "audit", "audit_log.jsonl"))
IDEMPOTENCY_STORE_FILE = os.getenv("IDEMPOTENCY_STORE_FILE", os.path.join(os.path.dirname(os.path.dirname(__file__)), "audit", "idempotency_store.json"))


class AuditManager:
    """Manages append-only cryptographic audit logging with SHA-256 hash chaining."""

    def __init__(self, log_path: str = AUDIT_LOG_FILE):
        self.log_path = log_path
        os.makedirs(os.path.dirname(os.path.abspath(self.log_path)), exist_ok=True)
        self._lock = threading.Lock()
        self._last_hash = self._recover_last_hash()

    def _recover_last_hash(self) -> str:
        """Reads the last line of the audit log to recover previous block hash."""
        if not os.path.exists(self.log_path):
            return "0" * 64
        try:
            with open(self.log_path, "r", encoding="utf-8") as f:
                lines = [line.strip() for line in f if line.strip()]
                if lines:
                    last_record = json.loads(lines[-1])
                    return last_record.get("hash", "0" * 64)
        except Exception as e:
            logger.warning(f"[AUDIT_MANAGER] Failed to recover last hash from {self.log_path}: {e}")
        return "0" * 64

    def record_event(
        self,
        event_type: str,
        actor: str,
        details: dict[str, Any],
        rationale: str = "",
        status: str = "COMPLETED"
    ) -> dict[str, Any]:
        """
        Appends an immutable, hash-chained audit event.
        Event types:
        - RECOMMENDATION_GENERATED
        - RECOMMENDATION_REJECTED
        - RECOMMENDATION_AUTHORIZED
        - RECOMMENDATION_APPLIED
        - PANIC_TRIGGERED
        - PANIC_RELEASED
        - ORDER_SUBMITTED
        - ORDER_BLOCKED
        """
        now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()
        with self._lock:
            payload = {
                "timestamp": now_iso,
                "event_type": event_type,
                "actor": actor,
                "status": status,
                "rationale": rationale,
                "details": details,
                "prev_hash": self._last_hash
            }
            serialized = json.dumps(payload, sort_keys=True)
            event_hash = hashlib.sha256(serialized.encode("utf-8")).hexdigest()
            payload["hash"] = event_hash
            self._last_hash = event_hash

            try:
                with open(self.log_path, "a", encoding="utf-8") as f:
                    f.write(json.dumps(payload) + "\n")
            except Exception as e:
                logger.error(f"[AUDIT_MANAGER] Failed writing audit record: {e}")

            return payload

    def verify_log_integrity(self) -> tuple[bool, int, str]:
        """
        Verifies the SHA-256 cryptographic hash chain across the full audit log.
        Returns (is_valid, record_count, details).
        """
        if not os.path.exists(self.log_path):
            return True, 0, "Log file does not exist yet."

        with self._lock:
            try:
                count = 0
                expected_prev = "0" * 64
                with open(self.log_path, "r", encoding="utf-8") as f:
                    for idx, line in enumerate(f, start=1):
                        line = line.strip()
                        if not line:
                            continue
                        rec = json.loads(line)
                        actual_hash = rec.get("hash")
                        prev_hash = rec.get("prev_hash")

                        if prev_hash != expected_prev:
                            return False, count, f"Hash chain broken at line {idx}: expected prev_hash {expected_prev}, got {prev_hash}"

                        # Recompute hash
                        rec_copy = {k: v for k, v in rec.items() if k != "hash"}
                        computed_hash = hashlib.sha256(json.dumps(rec_copy, sort_keys=True).encode("utf-8")).hexdigest()
                        if computed_hash != actual_hash:
                            return False, count, f"Hash mismatch at line {idx}: recomputed {computed_hash} != stored {actual_hash}"

                        expected_prev = actual_hash
                        count += 1

                return True, count, "Integrity verified. Chain valid."
            except Exception as e:
                return False, 0, f"Error validating audit integrity: {e}"


class IdempotencyStore:
    """Tracks and caches idempotent requests to prevent duplicate order or parameter changes."""

    def __init__(self, store_path: str = IDEMPOTENCY_STORE_FILE):
        self.store_path = store_path
        os.makedirs(os.path.dirname(os.path.abspath(self.store_path)), exist_ok=True)
        self._lock = threading.Lock()
        self._cache: dict[str, dict[str, Any]] = {}
        self._load()

    def _load(self):
        if not os.path.exists(self.store_path):
            return
        try:
            with open(self.store_path, "r", encoding="utf-8") as f:
                self._cache = json.load(f)
        except Exception as e:
            logger.warning(f"[IDEMPOTENCY_STORE] Failed loading store: {e}")
            self._cache = {}

    def _persist(self):
        try:
            tmp = self.store_path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self._cache, f, indent=2)
            os.replace(tmp, self.store_path)
        except Exception as e:
            logger.error(f"[IDEMPOTENCY_STORE] Failed persisting cache: {e}")

    def check_and_record(
        self,
        idempotency_key: str,
        request_type: str,
        payload_hash: str | None = None
    ) -> tuple[bool, dict[str, Any] | None]:
        """
        Checks if the idempotency_key has already been processed.
        Returns (is_duplicate, cached_response_or_record).
        If not duplicate, records the initial pending state.
        """
        if not idempotency_key:
            return False, None

        with self._lock:
            if idempotency_key in self._cache:
                cached = self._cache[idempotency_key]
                # If cached entry was PENDING for over 60s, expire it so failed/timed-out attempts do not permanently block retries
                if cached.get("status") == "PENDING":
                    c_time = cached.get("created_at")
                    is_stale = False
                    if c_time:
                        try:
                            created_dt = datetime.datetime.fromisoformat(str(c_time).replace("Z", "+00:00"))
                            if created_dt.tzinfo is None:
                                created_dt = created_dt.replace(tzinfo=datetime.timezone.utc)
                            if (datetime.datetime.now(datetime.timezone.utc) - created_dt).total_seconds() > 60:
                                is_stale = True
                        except Exception:
                            is_stale = True
                    if is_stale:
                        del self._cache[idempotency_key]
                    else:
                        logger.info(f"[IDEMPOTENCY_STORE] In-flight request in progress for key '{idempotency_key}'.")
                        return True, cached
                else:
                    logger.info(f"[IDEMPOTENCY_STORE] Duplicate request detected for key '{idempotency_key}'.")
                    return True, cached

            # Record pending entry
            record = {
                "key": idempotency_key,
                "request_type": request_type,
                "payload_hash": payload_hash or "",
                "status": "PENDING",
                "created_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                "response": None
            }
            self._cache[idempotency_key] = record
            self._persist()
            return False, record

    def complete_request(self, idempotency_key: str, response: dict[str, Any]):
        """Stores the completed execution result under the idempotency key."""
        if not idempotency_key:
            return

        with self._lock:
            if idempotency_key in self._cache:
                self._cache[idempotency_key]["status"] = "COMPLETED"
                self._cache[idempotency_key]["response"] = response
                self._cache[idempotency_key]["completed_at"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
            else:
                self._cache[idempotency_key] = {
                    "key": idempotency_key,
                    "status": "COMPLETED",
                    "response": response,
                    "completed_at": datetime.datetime.now(datetime.timezone.utc).isoformat()
                }
            self._persist()

    def remove(self, idempotency_key: str):
        """Removes a key from cache if execution failed before placing an order."""
        if not idempotency_key:
            return

        with self._lock:
            if idempotency_key in self._cache:
                del self._cache[idempotency_key]
                self._persist()



# Global singletons
_audit_manager: AuditManager | None = None
_idempotency_store: IdempotencyStore | None = None
_init_lock = threading.Lock()


def get_audit_manager() -> AuditManager:
    global _audit_manager
    if _audit_manager is None:
        with _init_lock:
            if _audit_manager is None:
                _audit_manager = AuditManager()
    return _audit_manager


def get_idempotency_store() -> IdempotencyStore:
    global _idempotency_store
    if _idempotency_store is None:
        with _init_lock:
            if _idempotency_store is None:
                _idempotency_store = IdempotencyStore()
    return _idempotency_store
