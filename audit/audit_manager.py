"""
audit/audit_manager.py — Cryptographic audit trail & idempotency store.

Guarantees:
1. Append-only SHA-256 hash-chained audit logging for recommendations, rejections,
   authorizations, parameter applications, and panic events.
2. Idempotency management for order dispatch and parameter modifications.

Persistence semantics:

* Every audit append runs under :func:`atomic_io.locked_path` (thread RLock +
  ``flock`` on ``<log>.lock``) and chains to the hash of the last valid record
  **on disk**, so several processes (dashboard, runners, CLI tools) extend one
  verifiable chain instead of forking it from their own in-memory head.
* The cached head hash only advances after the record was written. A failed
  write is reported to the caller (``persisted: False`` or
  :class:`AuditWriteError` when ``required=True``) instead of being presented
  as an audited event.
* A torn final line (crash mid-append) is preserved in
  ``<log>.corrupt-torn-<UTC>`` and cut from the log before the next append;
  :meth:`AuditManager.verify_log_integrity` reports a torn tail explicitly.
* The idempotency store is validated on load (JSON object of objects); a corrupt
  store is quarantined with a critical log instead of silently becoming empty.
  Mutations are cross-process safe (locked reload → apply → atomic replace) and
  records older than ``IDEMPOTENCY_RETENTION_DAYS`` (default 30) are pruned so
  the file cannot grow without bound.
"""

from __future__ import annotations

import datetime
import decimal
import hashlib
import json
import math
import os
import re
import threading
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from typing import Any, BinaryIO, TypeVar

from atomic_io import (
    atomic_write_bytes,
    atomic_write_json,
    load_json_state,
    locked_path,
    quarantine_file,
)
from logger import get_logger

logger = get_logger("audit_manager")

AUDIT_LOG_FILE = os.getenv("AUDIT_LOG_FILE", os.path.join(os.path.dirname(os.path.dirname(__file__)), "audit", "audit_log.jsonl"))
IDEMPOTENCY_STORE_FILE = os.getenv("IDEMPOTENCY_STORE_FILE", os.path.join(os.path.dirname(os.path.dirname(__file__)), "audit", "idempotency_store.json"))

GENESIS_HASH = "0" * 64
_HASH_RE = re.compile(r"^[0-9a-f]{64}$")
_TAIL_CHUNK_BYTES = 64 * 1024
# Upper bound on how far back record_event searches for the chain head. A log
# whose last 16 MiB contain no valid record is not something to append to.
MAX_TAIL_SCAN_BYTES = 16 * 1024 * 1024

_T = TypeVar("_T")


class AuditWriteError(RuntimeError):
    """An audit record could not be persisted (raised when ``required=True``)."""


class AuditChainError(AuditWriteError):
    """The current chain head on disk could not be determined safely."""


def _utcnow() -> datetime.datetime:
    return datetime.datetime.now(datetime.timezone.utc)


def _utc_stamp() -> str:
    return _utcnow().strftime("%Y%m%dT%H%M%S%fZ")


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        value = float(raw)
    except ValueError:
        logger.warning("[AUDIT_MANAGER] Ignoring non-numeric %s=%r; using %s", name, raw, default)
        return default
    if not math.isfinite(value) or value < 0:
        logger.warning("[AUDIT_MANAGER] Ignoring invalid %s=%r; using %s", name, raw, default)
        return default
    return value


def _env_flag(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _record_hash(record: dict[str, Any]) -> str:
    body = {key: value for key, value in record.items() if key != "hash"}
    return hashlib.sha256(json.dumps(body, sort_keys=True).encode("utf-8")).hexdigest()


def _parse_record(raw: bytes) -> dict[str, Any] | None:
    """Parse one audit line; ``None`` unless it is an object with a well-formed hash."""
    try:
        # Plain json.loads on purpose: records written before NaN sanitizing
        # may contain NaN tokens, and their hashes were computed over them.
        record = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError, RecursionError):
        return None
    if not isinstance(record, dict):
        return None
    stored = record.get("hash")
    if not isinstance(stored, str) or not _HASH_RE.match(stored):
        return None
    return record


def _iter_lines_reverse(handle: BinaryIO, end: int, max_scan: int) -> Iterator[bytes]:
    """Yield the lines of ``handle[0:end]`` from last to first (newlines stripped).

    Reads backwards in fixed-size chunks so locating the chain head costs
    O(tail) rather than O(file). Raises :class:`AuditChainError` once more than
    ``max_scan`` bytes were examined.
    """
    position = end
    carry = b""
    scanned = 0
    while position > 0:
        if scanned >= max_scan:
            raise AuditChainError(f"no valid audit record within the last {max_scan} bytes")
        step = min(_TAIL_CHUNK_BYTES, position)
        position -= step
        handle.seek(position)
        data = handle.read(step) + carry
        scanned += step
        parts = data.split(b"\n")
        carry = parts[0]
        yield from reversed(parts[1:])
    yield carry


@dataclass
class _Tail:
    """What the end of the audit log looks like right now."""

    head_hash: str
    has_valid_record: bool
    non_empty: bool
    torn_offset: int | None = None
    torn_fragment: bytes = b""
    needs_newline: bool = False


def _scan_tail(path: str, max_scan: int = MAX_TAIL_SCAN_BYTES) -> _Tail:
    try:
        handle = open(path, "rb")
    except FileNotFoundError:
        return _Tail(GENESIS_HASH, has_valid_record=False, non_empty=False)
    with handle:
        handle.seek(0, os.SEEK_END)
        end = handle.tell()
        if end == 0:
            return _Tail(GENESIS_HASH, has_valid_record=False, non_empty=False)
        handle.seek(end - 1)
        terminated = handle.read(1) == b"\n"
        tail = _Tail(GENESIS_HASH, has_valid_record=False, non_empty=True)
        skipped_corrupt = 0
        for index, raw in enumerate(_iter_lines_reverse(handle, end, max_scan)):
            if index == 0 and not terminated:
                record = _parse_record(raw.strip())
                if record is not None:
                    # A complete record that merely lacks its newline.
                    tail.needs_newline = True
                    tail.head_hash = record["hash"]
                    tail.has_valid_record = True
                    break
                tail.torn_offset = end - len(raw)
                tail.torn_fragment = raw
                continue
            if not raw.strip():
                continue
            record = _parse_record(raw.strip())
            if record is None:
                skipped_corrupt += 1
                continue
            tail.head_hash = record["hash"]
            tail.has_valid_record = True
            break
        if skipped_corrupt:
            logger.error(
                "[AUDIT_MANAGER] %d corrupt line(s) sit after the last valid record of %s; "
                "verify_log_integrity() will report them.",
                skipped_corrupt,
                path,
            )
        return tail


class AuditManager:
    """Manages append-only cryptographic audit logging with SHA-256 hash chaining."""

    def __init__(self, log_path: str = AUDIT_LOG_FILE, *, fsync: bool | None = None):
        self.log_path = log_path
        os.makedirs(os.path.dirname(os.path.abspath(self.log_path)), exist_ok=True)
        self._lock = threading.RLock()
        self.fsync = _env_flag("AUDIT_LOG_FSYNC", False) if fsync is None else bool(fsync)
        self.write_failures = 0
        self.last_write_error: str | None = None
        self.torn_tails_repaired = 0
        self._last_hash = self._recover_last_hash()

    @property
    def head_hash(self) -> str:
        """Hash of the newest record this instance knows was persisted."""
        return self._last_hash

    def _recover_last_hash(self) -> str:
        """Locate the hash of the last valid record (tail scan, not a full read)."""
        try:
            with locked_path(self.log_path):
                return _scan_tail(self.log_path).head_hash
        except (OSError, AuditChainError) as exc:
            logger.warning(f"[AUDIT_MANAGER] Failed to recover last hash from {self.log_path}: {exc}")
            return GENESIS_HASH

    def _repair_tail(self, tail: _Tail) -> None:
        """Preserve and cut a torn final fragment so the next record starts on a clean line."""
        if tail.torn_offset is None:
            return
        side_file = f"{self.log_path}.corrupt-torn-{_utc_stamp()}"
        atomic_write_bytes(side_file, tail.torn_fragment)
        with open(self.log_path, "r+b") as handle:
            handle.truncate(tail.torn_offset)
            handle.flush()
            os.fsync(handle.fileno())
        self.torn_tails_repaired += 1
        logger.error(
            "[AUDIT_MANAGER] Torn final line (%d bytes, interrupted append) moved from %s to %s",
            len(tail.torn_fragment),
            self.log_path,
            side_file,
        )

    def _chain_head_for_append(self) -> tuple[str, bool]:
        """Return (prev_hash, needs_newline) for the next record. Caller holds the file lock."""
        tail = _scan_tail(self.log_path)
        if tail.non_empty and not tail.has_valid_record and tail.torn_offset is None:
            # Content exists but not one line of it is a valid record: preserve
            # it and start a fresh chain rather than chaining onto garbage.
            moved = quarantine_file(self.log_path, "audit log contains no valid record")
            if moved is None and os.path.exists(self.log_path):
                raise AuditChainError(f"{self.log_path} has no valid record and could not be quarantined")
            logger.critical("[AUDIT_MANAGER] Audit log %s had no valid record; quarantined to %s", self.log_path, moved)
            return GENESIS_HASH, False
        if tail.torn_offset is not None:
            self._repair_tail(tail)
            if not tail.has_valid_record:
                # Only a torn fragment existed; the cut may leave earlier garbage.
                return self._chain_head_for_append()
        return tail.head_hash, tail.needs_newline

    def record_event(
        self,
        event_type: str,
        actor: str,
        details: dict[str, Any],
        rationale: str = "",
        status: str = "COMPLETED",
        *,
        required: bool = False,
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

        Returns the persisted record. If persistence fails the returned dict
        carries ``persisted: False`` and ``persist_error`` (and the record is
        *not* part of the chain); with ``required=True`` an
        :class:`AuditWriteError` is raised instead.
        """
        now_iso = _utcnow().isoformat()
        payload: dict[str, Any] = {
            "timestamp": now_iso,
            "event_type": event_type,
            "actor": actor,
            "status": status,
            "rationale": rationale,
            "details": _json_safe(details),
            "prev_hash": self._last_hash,
        }
        with self._lock:
            try:
                with locked_path(self.log_path):
                    prev_hash, needs_newline = self._chain_head_for_append()
                    payload["prev_hash"] = prev_hash
                    payload["hash"] = _record_hash(payload)
                    line = json.dumps(payload, allow_nan=False) + "\n"
                    with open(self.log_path, "a", encoding="utf-8") as handle:
                        handle.write(("\n" if needs_newline else "") + line)
                        handle.flush()
                        if self.fsync:
                            os.fsync(handle.fileno())
            except (OSError, ValueError, TypeError, AuditChainError) as exc:
                self.write_failures += 1
                self.last_write_error = str(exc)
                logger.critical(f"[AUDIT_MANAGER] Failed writing audit record {event_type}: {exc}")
                if required:
                    raise AuditWriteError(f"audit record {event_type} was not persisted: {exc}") from exc
                failed = dict(payload)
                failed.setdefault("hash", _record_hash(payload))
                failed["persisted"] = False
                failed["persist_error"] = str(exc)
                return failed
            self._last_hash = payload["hash"]
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
                with locked_path(self.log_path), open(self.log_path, "rb") as handle:
                    return self._verify_stream(handle)
            except OSError as exc:
                return False, 0, f"Error validating audit integrity: {exc}"

    @staticmethod
    def _verify_stream(handle: BinaryIO) -> tuple[bool, int, str]:
        count = 0
        expected_prev = GENESIS_HASH
        for idx, raw_line in enumerate(handle, start=1):
            terminated = raw_line.endswith(b"\n")
            line = raw_line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line.decode("utf-8"))
            except (UnicodeDecodeError, ValueError, RecursionError) as exc:
                if not terminated:
                    return False, count, (
                        f"Torn final line {idx} (interrupted append, {len(line)} bytes); "
                        f"{count} preceding record(s) verified"
                    )
                return False, count, f"Corrupt record at line {idx}: {exc}"
            if not isinstance(rec, dict):
                return False, count, f"Corrupt record at line {idx}: expected object, got {type(rec).__name__}"
            actual_hash = rec.get("hash")
            prev_hash = rec.get("prev_hash")

            if prev_hash != expected_prev:
                return False, count, f"Hash chain broken at line {idx}: expected prev_hash {expected_prev}, got {prev_hash}"

            computed_hash = _record_hash(rec)
            if computed_hash != actual_hash:
                return False, count, f"Hash mismatch at line {idx}: recomputed {computed_hash} != stored {actual_hash}"

            expected_prev = actual_hash
            count += 1

        return True, count, "Integrity verified. Chain valid."


def _json_safe(value: Any, _depth: int = 0) -> Any:
    """Return a JSON-serializable copy of ``value`` that never contains NaN/inf.

    Non-finite floats become ``None`` (strict JSON has no spelling for them and
    a store containing ``NaN`` would be rejected as corrupt on the next load).
    """
    if _depth > 64:
        return str(value)
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, decimal.Decimal):
        return str(value) if value.is_finite() else None
    if isinstance(value, (datetime.datetime, datetime.date)):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(key): _json_safe(item, _depth + 1) for key, item in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_json_safe(item, _depth + 1) for item in value]
    return str(value)


def _parse_ts(value: Any) -> datetime.datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=datetime.timezone.utc)
    return parsed


_PENDING = "PENDING"
_COMPLETED = "COMPLETED"
_EPOCH = datetime.datetime(1970, 1, 1, tzinfo=datetime.timezone.utc)


class IdempotencyStore:
    """Tracks and caches idempotent requests to prevent duplicate order or parameter changes."""

    def __init__(
        self,
        store_path: str = IDEMPOTENCY_STORE_FILE,
        *,
        retention_days: float | None = None,
        pending_ttl_seconds: float = 60.0,
        max_entries: int | None = None,
    ):
        self.store_path = store_path
        os.makedirs(os.path.dirname(os.path.abspath(self.store_path)), exist_ok=True)
        self._lock = threading.RLock()
        self.retention_days = _env_float("IDEMPOTENCY_RETENTION_DAYS", 30.0) if retention_days is None else float(retention_days)
        self.pending_ttl_seconds = float(pending_ttl_seconds)
        self.max_entries = int(_env_float("IDEMPOTENCY_MAX_ENTRIES", 50_000)) if max_entries is None else int(max_entries)
        self.load_status = "missing"
        self.quarantined_to: str | None = None
        self.invalid_entries_dropped = 0
        self.persist_failures = 0
        self.pruned_total = 0
        # Local changes that could not be written yet (re-applied on the next mutation).
        self._unpersisted: dict[str, dict[str, Any]] = {}
        self._unpersisted_deletes: set[str] = set()
        self._cache: dict[str, dict[str, Any]] = {}
        self._load()

    # ── disk access ─────────────────────────────────────────────────────────
    def _load(self) -> None:
        with self._lock, locked_path(self.store_path):
            self._cache = self._read_disk()

    def _read_disk(self) -> dict[str, dict[str, Any]]:
        """Read and validate the store. Caller must hold the file lock."""
        result = load_json_state(self.store_path, expected_type=dict)
        self.load_status = result.status
        if result.status == "corrupt":
            self.quarantined_to = result.quarantined_to
            logger.critical(
                "[IDEMPOTENCY_STORE] Store %s is corrupt (%s); quarantined to %s. "
                "Keys recorded before this point are no longer deduplicated.",
                self.store_path,
                result.error,
                result.quarantined_to,
            )
            return {}
        entries: dict[str, dict[str, Any]] = {}
        dropped = 0
        for key, value in result.data.items():
            if isinstance(key, str) and key and isinstance(value, dict):
                entries[key] = value
            else:
                dropped += 1
        if dropped:
            self.invalid_entries_dropped += dropped
            logger.error("[IDEMPOTENCY_STORE] Dropped %d malformed record(s) from %s", dropped, self.store_path)
        return entries

    def _mutate(self, operation: Callable[[dict[str, dict[str, Any]], datetime.datetime], tuple[_T, bool]]) -> _T:
        """Locked reload → apply ``operation`` → prune → atomic replace.

        ``operation(entries, now)`` returns ``(result, changed)``. The store is
        only rewritten when something changed (including pruning).
        """
        with self._lock, locked_path(self.store_path):
            now = _utcnow()
            entries = self._read_disk()
            for key in self._unpersisted_deletes:
                entries.pop(key, None)
            for key, value in self._unpersisted.items():
                entries.setdefault(key, value)
            keys_before = set(entries)
            result, changed = operation(entries, now)
            pruned = self._prune(entries, now)
            if changed or pruned or self._unpersisted or self._unpersisted_deletes:
                try:
                    atomic_write_json(self.store_path, entries, indent=2, allow_nan=False)
                except (OSError, TypeError, ValueError) as exc:
                    self.persist_failures += 1
                    self._unpersisted = dict(entries)
                    self._unpersisted_deletes |= keys_before - set(entries)
                    logger.critical(
                        f"[IDEMPOTENCY_STORE] Failed persisting {self.store_path}: {exc}. "
                        "Deduplication continues from memory in this process only."
                    )
                else:
                    self._unpersisted.clear()
                    self._unpersisted_deletes.clear()
            self._cache = entries
            return result

    def _prune(self, entries: dict[str, dict[str, Any]], now: datetime.datetime) -> int:
        removed = 0
        if self.retention_days > 0:
            cutoff = now - datetime.timedelta(days=self.retention_days)
            for key in list(entries):
                record = entries[key]
                stamp = _parse_ts(record.get("completed_at")) or _parse_ts(record.get("created_at"))
                if stamp is None or stamp < cutoff:
                    del entries[key]
                    removed += 1
        if self.max_entries > 0 and len(entries) > self.max_entries:
            completed = sorted(
                (key for key, record in entries.items() if record.get("status") == _COMPLETED),
                key=lambda key: _parse_ts(entries[key].get("completed_at")) or _EPOCH,
            )
            for key in completed[: len(entries) - self.max_entries]:
                del entries[key]
                removed += 1
        if removed:
            self.pruned_total += removed
            logger.info("[IDEMPOTENCY_STORE] Pruned %d expired record(s)", removed)
        return removed

    def _pending_is_stale(self, record: dict[str, Any], now: datetime.datetime) -> bool:
        created = _parse_ts(record.get("created_at"))
        if created is None:
            return True
        return (now - created).total_seconds() > self.pending_ttl_seconds

    # ── public API ──────────────────────────────────────────────────────────
    def check_and_record(
        self,
        idempotency_key: str,
        request_type: str,
        payload_hash: str | None = None
    ) -> tuple[bool, dict[str, Any] | None]:
        """
        Checks if the idempotency_key has already been processed (by any process
        sharing the store). Returns (is_duplicate, cached_response_or_record).
        If not duplicate, records the initial pending state.
        """
        if not idempotency_key:
            return False, None
        key = str(idempotency_key)

        def operation(entries: dict[str, dict[str, Any]], now: datetime.datetime) -> tuple[tuple[bool, dict[str, Any] | None], bool]:
            cached = entries.get(key)
            if cached is not None:
                # A PENDING entry older than the TTL belongs to a failed/timed-out
                # attempt; expire it so it does not block retries forever.
                if cached.get("status") == _PENDING and self._pending_is_stale(cached, now):
                    logger.warning(f"[IDEMPOTENCY_STORE] Expiring stale pending record for key '{key}'.")
                    del entries[key]
                elif cached.get("status") == _PENDING:
                    logger.info(f"[IDEMPOTENCY_STORE] In-flight request in progress for key '{key}'.")
                    return (True, dict(cached)), False
                else:
                    logger.info(f"[IDEMPOTENCY_STORE] Duplicate request detected for key '{key}'.")
                    return (True, dict(cached)), False

            record = {
                "key": key,
                "request_type": str(request_type),
                "payload_hash": payload_hash or "",
                "status": _PENDING,
                "created_at": now.isoformat(),
                "response": None,
            }
            entries[key] = record
            return (False, dict(record)), True

        return self._mutate(operation)

    def complete_request(self, idempotency_key: str, response: dict[str, Any]) -> None:
        """Stores the completed execution result under the idempotency key."""
        if not idempotency_key:
            return
        key = str(idempotency_key)
        safe_response = _json_safe(response)

        def operation(entries: dict[str, dict[str, Any]], now: datetime.datetime) -> tuple[None, bool]:
            existing = entries.get(key)
            record = dict(existing) if isinstance(existing, dict) else {"key": key}
            record["status"] = _COMPLETED
            record["response"] = safe_response
            record["completed_at"] = now.isoformat()
            entries[key] = record
            return None, True

        self._mutate(operation)

    def remove(self, idempotency_key: str, *, force: bool = False) -> bool:
        """Release a PENDING key after an attempt failed before reaching the venue.

        COMPLETED records are kept unless ``force=True``: erasing them would
        let a retry of an already-executed request run a second time.
        Returns True when a record was removed.
        """
        if not idempotency_key:
            return False
        key = str(idempotency_key)

        def operation(entries: dict[str, dict[str, Any]], now: datetime.datetime) -> tuple[bool, bool]:
            record = entries.get(key)
            if record is None:
                return False, False
            if record.get("status") == _COMPLETED and not force:
                logger.warning(f"[IDEMPOTENCY_STORE] Refusing to remove COMPLETED record for key '{key}'.")
                return False, False
            del entries[key]
            return True, True

        return self._mutate(operation)

    def get(self, idempotency_key: str) -> dict[str, Any] | None:
        """Fresh (cross-process) read of one record."""
        key = str(idempotency_key)
        with self._lock, locked_path(self.store_path):
            entries = self._read_disk()
            for deleted in self._unpersisted_deletes:
                entries.pop(deleted, None)
            for pending_key, value in self._unpersisted.items():
                entries.setdefault(pending_key, value)
            self._cache = entries
            record = entries.get(key)
            return dict(record) if record is not None else None

    def status(self) -> dict[str, Any]:
        with self._lock:
            return {
                "store_path": self.store_path,
                "load_status": self.load_status,
                "quarantined_to": self.quarantined_to,
                "entries": len(self._cache),
                "invalid_entries_dropped": self.invalid_entries_dropped,
                "persist_failures": self.persist_failures,
                "pruned_total": self.pruned_total,
                "retention_days": self.retention_days,
                "unpersisted_changes": len(self._unpersisted) + len(self._unpersisted_deletes),
            }


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


def persisted_hash(record: dict[str, Any] | None) -> str | None:
    """Hash of an audit record returned by ``record_event`` — ``None`` if it was not persisted."""
    if not isinstance(record, dict) or record.get("persisted") is False:
        return None
    value = record.get("hash")
    return value if isinstance(value, str) else None
