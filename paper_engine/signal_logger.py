import json
import logging
import math
import os
import time
import uuid
from dataclasses import dataclass

from atomic_io import append_jsonl, atomic_write_json, load_json_state, locked_path

logger = logging.getLogger("paper_engine.signal_logger")


@dataclass
class Signal:
    """
    Standardized Signal interface for all strategies.
    No Lookahead Bias: This must be generated strictly from data <= signal_time.
    """
    symbol: str
    timeframe: str
    direction: str
    confidence: float
    signal_time: float
    reference_price: float
    strategy_name: str
    action: str  # "ENTRY", "EXIT", "REVERSE"
    reason: str
    features: dict | None = None


class PaperSignalLogger:
    """
    Forward Validation Dataset Logger (JSON list on disk).
    Every signal is recorded here, even if rejected by Risk Limits or Margin.
    This creates the next out-of-sample dataset for future model training.

    Persistence: a corrupt dataset file is quarantined (never overwritten with
    an empty list), writes are atomic and locked, and signal ids are unique
    even when several signals arrive within the same millisecond.
    """
    def __init__(self, filename="paper_signals.json"):
        self.filename = filename
        self.signals: list[dict] = []
        self.load_status = "missing"
        self.quarantined_to: str | None = None
        os.makedirs(os.path.dirname(self.filename) or '.', exist_ok=True)
        self._load()

    def log_signal(self, signal: Signal, execution_result: str = "PENDING"):
        record = {
            "signal_id": f"sig_{int(time.time() * 1000)}_{uuid.uuid4().hex[:8]}",
            "timestamp": signal.signal_time,
            "symbol": signal.symbol,
            "timeframe": signal.timeframe,
            "strategy": signal.strategy_name,
            "direction": signal.direction,
            "action": signal.action,
            "confidence": getattr(signal, 'confidence', getattr(signal, 'win_rate_prior', 0.5)),
            "reference_price": signal.reference_price,
            "reason": signal.reason,
            "features": signal.features or {},
            "execution_result": execution_result,
            "eventual_outcome": None  # To be labeled later
        }
        self.signals.append(record)
        self._save()
        return record["signal_id"]

    def update_outcome(self, signal_id: str, outcome: str) -> bool:
        for sig in self.signals:
            if sig.get("signal_id") == signal_id:
                sig["eventual_outcome"] = outcome
                self._save()
                return True
        return False

    def _save(self):
        with locked_path(self.filename):
            # NaN/inf are replaced by null: strict JSON cannot represent them and
            # the dataset would otherwise be rejected as corrupt on reload.
            atomic_write_json(self.filename, [_finite_json(item) for item in self.signals], indent=4)

    def _load(self):
        with locked_path(self.filename):
            result = load_json_state(self.filename, expected_type=list)
        self.load_status = result.status
        if result.status == "corrupt":
            self.quarantined_to = result.quarantined_to
            logger.critical(
                "Signal dataset %s is corrupt (%s); preserved as %s and a new dataset is started",
                self.filename, result.error, result.quarantined_to,
            )
            return
        self.signals = [item for item in result.data if isinstance(item, dict)]
        dropped = len(result.data) - len(self.signals)
        if dropped:
            logger.error("Dropped %d non-object record(s) from %s", dropped, self.filename)


def _finite_json(value):
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, dict):
        return {key: _finite_json(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_finite_json(item) for item in value]
    return value


class SignalLogger:
    """
    Append-only JSONL signal logger with deduplication.
    Rejects duplicate signal_ids to prevent double-recording — also across
    processes sharing the file: before each append the bytes written by other
    writers since the last look are scanned (under the file lock) for ids.
    Used by adversarial tests and forward validator.
    """

    def __init__(self, filename: str = "signals.jsonl"):
        self.filename = filename
        self._seen_ids: set[str] = set()
        self._offset = 0
        self.skipped_lines = 0
        self._load_seen_ids()

    def _load_seen_ids(self):
        with locked_path(self.filename):
            self._refresh_seen_ids()

    def _refresh_seen_ids(self) -> None:
        """Index ids from complete lines appended since ``self._offset``. Caller holds the lock."""
        try:
            handle = open(self.filename, "rb")
        except FileNotFoundError:
            self._offset = 0
            return
        with handle:
            handle.seek(0, os.SEEK_END)
            size = handle.tell()
            if size < self._offset:  # file was replaced/truncated: re-index from scratch
                self._offset = 0
                self._seen_ids.clear()
            handle.seek(self._offset)
            chunk = handle.read(size - self._offset)
        complete_end = chunk.rfind(b"\n") + 1  # a torn tail is left for the next look
        for raw in chunk[:complete_end].split(b"\n"):
            if not raw.strip():
                continue
            try:
                record = json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, ValueError):
                self.skipped_lines += 1
                continue
            sid = record.get("signal_id") if isinstance(record, dict) else None
            if isinstance(sid, str) and sid:
                self._seen_ids.add(sid)
        self._offset += complete_end
        if self.skipped_lines:
            logger.warning("%d unreadable line(s) skipped while indexing %s", self.skipped_lines, self.filename)

    def log_signal(self, signal: dict) -> bool:
        """
        Write a signal to the JSONL log.
        Returns True if written, False if duplicate (idempotent).
        Non-finite numbers are stored as null (strict JSON has no NaN), so a
        bad feature value can neither crash the caller nor poison the file.
        """
        sig_id = signal.get("signal_id")
        record = _finite_json(dict(signal))
        if "logged_at" not in record:
            record["logged_at"] = time.time()
        with locked_path(self.filename):
            self._refresh_seen_ids()
            if sig_id and sig_id in self._seen_ids:
                return False  # idempotent — duplicate, not written again
            append_jsonl(self.filename, record)
            # Our own line is indexed directly; advance past it on the next refresh.
            if sig_id:
                self._seen_ids.add(sig_id)
        return True

    def has_signal_id(self, signal_id: str) -> bool:
        """Report whether a stable event ID is already present in the log."""
        return bool(signal_id) and signal_id in self._seen_ids
