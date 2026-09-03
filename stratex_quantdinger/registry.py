"""Immutable strategy/version registry.

A strategy is not a mutable blob at runtime. Every deployed or researched version
has an explicit version and source hash for strict quantitative reproducibility.
"""

from __future__ import annotations
import hashlib
import json
from pathlib import Path
from datetime import datetime, timezone
from typing import Any

from .models import StrategyVersion, AuditEvent


VALID_STATUSES = {"RESEARCH", "OOS_VALIDATED", "APPROVED", "ACTIVE", "RETIRED"}

# Strict state machine transition rules
ALLOWED_TRANSITIONS = {
    "RESEARCH": {"OOS_VALIDATED", "RETIRED"},
    "OOS_VALIDATED": {"APPROVED", "RETIRED"},
    "APPROVED": {"ACTIVE", "RETIRED"},
    "ACTIVE": {"RETIRED"},
    "RETIRED": set(),  # Terminal state
}


class StrategyRegistry:
    def __init__(self, path: str = "strategy_registry.json", audit_log_path: str = "quantdinger_audit.jsonl"):
        self.path = Path(path)
        self.audit_log_path = Path(audit_log_path)

    def _load(self) -> dict:
        if not self.path.exists():
            return {"strategies": {}}
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except Exception:
            return {"strategies": {}}

    def _save(self, data: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")
        tmp.replace(self.path)

    def _audit(self, event: AuditEvent) -> None:
        self.audit_log_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.audit_log_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(event.__dict__) + "\n")

    @staticmethod
    def compute_source_hash(source: str) -> str:
        """Computes SHA-256 hash of normalized source string."""
        return hashlib.sha256(source.strip().encode("utf-8")).hexdigest()

    def register(
        self,
        strategy_id: str,
        version: str,
        source: str,
        parameters: dict[str, Any],
        status: str = "RESEARCH",
    ) -> StrategyVersion:
        """Registers a new strategy version. If version already exists, verifies immutability."""
        if status not in VALID_STATUSES:
            raise ValueError(f"Invalid status '{status}'. Valid statuses: {VALID_STATUSES}")

        data = self._load()
        strategy = data["strategies"].setdefault(strategy_id, {})
        source_hash = self.compute_source_hash(source)

        if version in strategy:
            existing = strategy[version]
            if existing["source_hash"] != source_hash:
                raise ValueError(
                    f"Immutable strategy version conflict for {strategy_id}@{version}: "
                    f"Source hash differs (existing: {existing['source_hash'][:8]}, new: {source_hash[:8]})."
                )
            if existing.get("parameters") != parameters:
                raise ValueError(
                    f"Immutable strategy version conflict for {strategy_id}@{version}: "
                    f"Parameters differ for frozen version."
                )
            return StrategyVersion(**existing)

        now = datetime.now(timezone.utc).isoformat()
        obj = StrategyVersion(
            strategy_id=strategy_id,
            version=version,
            source_hash=source_hash,
            created_at=now,
            parameters=parameters,
            status=status,
        )
        strategy[version] = obj.__dict__
        self._save(data)

        self._audit(AuditEvent(
            timestamp=now,
            actor="system",
            resource=f"strategy:{strategy_id}:{version}",
            action="REGISTER_VERSION",
            previous_state=None,
            new_state=status,
            reason="New strategy version registered",
            correlation_id=f"strategy={strategy_id} version={version}",
        ))
        return obj

    def get(self, strategy_id: str, version: str) -> StrategyVersion:
        data = self._load()
        try:
            return StrategyVersion(**data["strategies"][strategy_id][version])
        except KeyError as e:
            raise KeyError(f"Unknown strategy version: {strategy_id}@{version}") from e

    def get_active(self, strategy_id: str) -> StrategyVersion | None:
        """Returns the currently ACTIVE version for a strategy, if any."""
        data = self._load()
        versions = data.get("strategies", {}).get(strategy_id, {})
        for ver, info in versions.items():
            if info.get("status") == "ACTIVE":
                return StrategyVersion(**info)
        return None

    def list_versions(self, strategy_id: str | None = None, status: str | None = None) -> list[StrategyVersion]:
        data = self._load()
        results = []
        for s_id, v_map in data.get("strategies", {}).items():
            if strategy_id is not None and s_id != strategy_id:
                continue
            for ver, info in v_map.items():
                if status is not None and info.get("status") != status:
                    continue
                results.append(StrategyVersion(**info))
        return results

    def promote(
        self,
        strategy_id: str,
        version: str,
        new_status: str,
        actor: str = "operator",
        reason: str = "",
    ) -> StrategyVersion:
        """Explicitly promotes a strategy version according to the lifecycle state machine."""
        if new_status not in VALID_STATUSES:
            raise ValueError(f"Invalid status '{new_status}'. Valid statuses: {VALID_STATUSES}")

        data = self._load()
        if strategy_id not in data.get("strategies", {}) or version not in data["strategies"][strategy_id]:
            raise KeyError(f"Unknown strategy version: {strategy_id}@{version}")

        current_info = data["strategies"][strategy_id][version]
        current_status = current_info.get("status", "RESEARCH")

        if new_status != current_status:
            allowed = ALLOWED_TRANSITIONS.get(current_status, set())
            if new_status not in allowed:
                raise ValueError(
                    f"Illegal lifecycle transition for {strategy_id}@{version}: "
                    f"Cannot transition from {current_status} to {new_status}. Allowed: {allowed}"
                )

        now = datetime.now(timezone.utc).isoformat()

        # Rule: Only one ACTIVE version per strategy at a time.
        # If promoting to ACTIVE, retire any currently ACTIVE version.
        if new_status == "ACTIVE":
            for other_ver, other_info in data["strategies"][strategy_id].items():
                if other_ver != version and other_info.get("status") == "ACTIVE":
                    other_info["status"] = "RETIRED"
                    self._audit(AuditEvent(
                        timestamp=now,
                        actor=actor,
                        resource=f"strategy:{strategy_id}:{other_ver}",
                        action="SUPERSEDED_RETIRED",
                        previous_state="ACTIVE",
                        new_state="RETIRED",
                        reason=f"Superseded by new active version {version}",
                        correlation_id=f"strategy={strategy_id} version={other_ver}",
                    ))

        current_info["status"] = new_status
        self._save(data)

        self._audit(AuditEvent(
            timestamp=now,
            actor=actor,
            resource=f"strategy:{strategy_id}:{version}",
            action="PROMOTE",
            previous_state=current_status,
            new_state=new_status,
            reason=reason or f"Promoted from {current_status} to {new_status}",
            correlation_id=f"strategy={strategy_id} version={version}",
        ))
        return StrategyVersion(**current_info)
