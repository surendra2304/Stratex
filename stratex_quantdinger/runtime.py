"""Runtime lease/heartbeat primitives and health supervisor for strategy workers."""

from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
import logging
import os
import threading

from atomic_io import atomic_write_json, load_json_state, locked_path

from .models import RuntimeHeartbeat, AuditEvent

logger = logging.getLogger("stratex_quantdinger.runtime")


def default_leases_path() -> str:
    """Runtime lease/heartbeat file (``RUNTIME_LEASES_FILE``, default ``runtime_leases.json``)."""
    return os.getenv("RUNTIME_LEASES_FILE", "runtime_leases.json")


@dataclass
class RuntimeLease:
    """Represents a time-bounded operational lease for an active strategy execution runtime."""
    runtime_id: str
    strategy_id: str
    lease_seconds: int = 30
    expires_at: datetime | None = None

    def acquire(self) -> RuntimeHeartbeat:
        self.expires_at = datetime.now(timezone.utc) + timedelta(seconds=self.lease_seconds)
        return self.heartbeat("RUNNING")

    def heartbeat(self, status: str = "RUNNING") -> RuntimeHeartbeat:
        now = datetime.now(timezone.utc)
        self.expires_at = now + timedelta(seconds=self.lease_seconds)
        return RuntimeHeartbeat(
            runtime_id=self.runtime_id,
            strategy_id=self.strategy_id,
            status=status,
            timestamp=now.isoformat(),
            lease_expires_at=self.expires_at.isoformat(),
        )

    def is_valid(self) -> bool:
        return self.expires_at is not None and datetime.now(timezone.utc) < self.expires_at


class RuntimeSupervisor:
    """Owns runtime health evaluation decisions; gates new entry execution intents."""

    def __init__(self, leases_path: str | None = None):
        self.leases_path = Path(leases_path or default_leases_path())
        self._lock = threading.Lock()

    def record_heartbeat(self, heartbeat: RuntimeHeartbeat) -> None:
        """Persists the latest heartbeat for observability."""
        with self._lock, locked_path(self.leases_path):
            # Observability-only file: a corrupt copy is quarantined (kept for
            # inspection) and rebuilt rather than blocking heartbeats.
            result = load_json_state(self.leases_path, expected_type=dict)
            if result.status == "corrupt":
                logger.error("Runtime lease file %s was corrupt (%s); quarantined to %s",
                             self.leases_path, result.error, result.quarantined_to)
            data = result.data if result.status == "ok" else {}
            data[heartbeat.runtime_id] = heartbeat.__dict__
            self.leases_path.parent.mkdir(parents=True, exist_ok=True)
            atomic_write_json(self.leases_path, data, indent=2, sort_keys=True, default=str)

    def evaluate(self, heartbeat: RuntimeHeartbeat | None) -> tuple[bool, str]:
        """Evaluates whether the runtime lease is healthy and permitted to issue new execution intents."""
        if heartbeat is None:
            return False, "NO_HEARTBEAT"
        if heartbeat.lease_expires_at is None:
            return False, "NO_LEASE"
        try:
            expires = datetime.fromisoformat(heartbeat.lease_expires_at)
            # Ensure timezone awareness
            if expires.tzinfo is None:
                expires = expires.replace(tzinfo=timezone.utc)
        except Exception:
            return False, "MALFORMED_LEASE_EXPIRY"

        if datetime.now(timezone.utc) >= expires:
            return False, "LEASE_EXPIRED"
        if heartbeat.status not in {"RUNNING", "PAUSED"}:
            return False, f"RUNTIME_NOT_HEALTHY_{heartbeat.status}"
        return True, "RUNTIME_OK"
