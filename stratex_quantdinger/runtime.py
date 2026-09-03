"""Runtime lease/heartbeat primitives and health supervisor for strategy workers."""

from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
import json
import threading

from .models import RuntimeHeartbeat, AuditEvent


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

    def __init__(self, leases_path: str = "runtime_leases.json"):
        self.leases_path = Path(leases_path)
        self._lock = threading.Lock()

    def record_heartbeat(self, heartbeat: RuntimeHeartbeat) -> None:
        """Persists the latest heartbeat for observability."""
        with self._lock:
            data = {}
            if self.leases_path.exists():
                try:
                    data = json.loads(self.leases_path.read_text(encoding="utf-8"))
                except Exception:
                    data = {}
            data[heartbeat.runtime_id] = heartbeat.__dict__
            self.leases_path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.leases_path.with_suffix(self.leases_path.suffix + ".tmp")
            tmp.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")
            tmp.replace(self.leases_path)

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
