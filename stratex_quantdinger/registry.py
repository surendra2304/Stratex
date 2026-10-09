"""Immutable strategy/version registry.

A strategy is not a mutable blob at runtime. Every deployed or researched version
has an explicit version and source hash for strict quantitative reproducibility.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from functools import wraps
from pathlib import Path
from typing import Any

try:
    import fcntl as _fcntl
except ImportError:  # Windows fallback
    _fcntl = None

try:
    import msvcrt as _msvcrt
except ImportError:  # POSIX fallback
    _msvcrt = None

from atomic_io import append_jsonl, atomic_write_text

from .models import StrategyVersion, AuditEvent
from .promotion_policy import PROMOTION_ELIGIBLE, evaluate_oos_metrics


VALID_STATUSES = {"RESEARCH", "OOS_VALIDATED", "APPROVED", "ACTIVE", "RETIRED"}


class RegistryIntegrityError(RuntimeError):
    """Raised when persisted registry state is corrupt or structurally unsafe."""


# Strict state machine transition rules
ALLOWED_TRANSITIONS = {
    "RESEARCH": {"OOS_VALIDATED", "RETIRED"},
    "OOS_VALIDATED": {"APPROVED", "RETIRED"},
    "APPROVED": {"ACTIVE", "RETIRED"},
    "ACTIVE": {"RETIRED"},
    "RETIRED": set(),  # Terminal state
}

_REGISTRY_THREAD_LOCKS: dict[str, Any] = {}
_REGISTRY_THREAD_LOCKS_GUARD = threading.Lock()


def _serialized_mutation(method):
    """Serialize each registry read-modify-write across threads and processes."""
    @wraps(method)
    def wrapped(self, *args, **kwargs):
        with self._mutation_lock():
            return method(self, *args, **kwargs)
    return wrapped


class StrategyRegistry:
    def __init__(self, path: str = "strategy_registry.json", audit_log_path: str = "quantdinger_audit.jsonl"):
        self.path = Path(path)
        self.audit_log_path = Path(audit_log_path)

    @contextmanager
    def _mutation_lock(self):
        """Acquire a per-path thread lock and an OS-level advisory file lock."""
        lock_path = self.path.with_suffix(self.path.suffix + ".lock")
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        lock_key = str(lock_path.resolve())
        with _REGISTRY_THREAD_LOCKS_GUARD:
            thread_lock = _REGISTRY_THREAD_LOCKS.setdefault(lock_key, threading.RLock())

        with thread_lock:
            with open(lock_path, "a+b") as lock_file:
                if _fcntl is not None:
                    _fcntl.flock(lock_file.fileno(), _fcntl.LOCK_EX)
                elif _msvcrt is not None:  # pragma: no cover - Windows
                    lock_file.seek(0)
                    if not lock_file.read(1):
                        lock_file.write(b"0")
                        lock_file.flush()
                    lock_file.seek(0)
                    _msvcrt.locking(lock_file.fileno(), _msvcrt.LK_LOCK, 1)
                else:  # Fail closed rather than race in an unsupported runtime.
                    raise RegistryIntegrityError("No inter-process file-lock backend is available")
                try:
                    yield
                finally:
                    if _fcntl is not None:
                        _fcntl.flock(lock_file.fileno(), _fcntl.LOCK_UN)
                    elif _msvcrt is not None:  # pragma: no cover - Windows
                        lock_file.seek(0)
                        _msvcrt.locking(lock_file.fileno(), _msvcrt.LK_UNLCK, 1)

    def _load(self) -> dict:
        if not self.path.exists():
            return {"strategies": {}}
        try:
            if self.path.stat().st_size > 50 * 1024 * 1024:
                raise RegistryIntegrityError("Persisted strategy registry exceeds the 50 MiB safety limit")
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except RegistryIntegrityError:
            raise
        except (OSError, UnicodeError, json.JSONDecodeError, RecursionError) as exc:
            # Treating a corrupt registry as empty lets the next registration
            # overwrite every existing version and promotion history.
            raise RegistryIntegrityError(
                "Persisted strategy registry is unreadable; refusing to reset it"
            ) from exc
        if not isinstance(data, dict) or not isinstance(data.get("strategies"), dict):
            raise RegistryIntegrityError("Persisted strategy registry has an invalid top-level schema")
        for strategy_id, versions in data["strategies"].items():
            if not isinstance(strategy_id, str) or not isinstance(versions, dict):
                raise RegistryIntegrityError("Persisted strategy registry contains an invalid strategy entry")
            for version, info in versions.items():
                if not isinstance(version, str) or not isinstance(info, dict):
                    raise RegistryIntegrityError("Persisted strategy registry contains an invalid version entry")
                required = {"strategy_id", "version", "source_hash", "created_at", "parameters"}
                allowed = required | {"status", "evidence"}
                if not required.issubset(info) or set(info) - allowed:
                    raise RegistryIntegrityError("Persisted strategy registry version has an invalid record schema")
                if info["strategy_id"] != strategy_id or info["version"] != version:
                    raise RegistryIntegrityError("Persisted strategy registry key does not match its record")
                source_hash = info.get("source_hash")
                if (
                    not isinstance(source_hash, str)
                    or not source_hash
                    or not all(char in "0123456789abcdefABCDEF" for char in source_hash)
                    or not isinstance(info.get("created_at"), str)
                    or not info["created_at"].strip()
                ):
                    raise RegistryIntegrityError("Persisted strategy registry version provenance is invalid")
                if not isinstance(info.get("parameters"), dict):
                    raise RegistryIntegrityError("Persisted strategy registry parameters are not an object")
                if not isinstance(info.get("evidence", {}), dict):
                    raise RegistryIntegrityError("Persisted strategy registry evidence is not an object")
                status = info.get("status", "RESEARCH")
                if not isinstance(status, str) or status not in VALID_STATUSES:
                    raise RegistryIntegrityError(f"Persisted strategy registry has invalid status: {status!r}")
                if status != "RESEARCH" and len(source_hash) != 64:
                    raise RegistryIntegrityError("Elevated registry status lacks a full SHA-256 source hash")
        return data

    def _save(self, data: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(data, indent=2, sort_keys=True)
        # Durable atomic replace (unique temp file, fsync of file and
        # directory): a crash can no longer leave a zero-length registry.
        atomic_write_text(self.path, payload)

    def _audit(self, event: AuditEvent) -> None:
        # One complete line per event; a torn tail left by a crash is sealed
        # off instead of being glued onto the next record.
        append_jsonl(self.audit_log_path, event.__dict__, fsync=True)

    @staticmethod
    def compute_source_hash(source: str) -> str:
        """Computes SHA-256 hash of normalized source string."""
        return hashlib.sha256(source.strip().encode("utf-8")).hexdigest()

    @_serialized_mutation
    def register(
        self,
        strategy_id: str,
        version: str,
        source: str,
        parameters: dict[str, Any],
        status: str = "RESEARCH",
        evidence: dict[str, Any] | None = None,
    ) -> StrategyVersion:
        """Registers a new immutable version as RESEARCH; later states need promotion."""
        if status != "RESEARCH":
            raise ValueError(
                "New strategy versions must be registered as RESEARCH; "
                "use promote() to pass the lifecycle evidence gates."
            )
        if not isinstance(strategy_id, str) or not strategy_id.strip():
            raise ValueError("strategy_id must be a non-empty string")
        if not isinstance(version, str) or not version.strip():
            raise ValueError("version must be a non-empty string")
        if not isinstance(source, str) or not source.strip():
            raise ValueError("source must be a non-empty string")
        if not isinstance(parameters, dict):
            raise ValueError("parameters must be a JSON object")
        if evidence is not None and not isinstance(evidence, dict):
            raise ValueError("evidence must be a JSON object")
        try:
            json.dumps({"parameters": parameters, "evidence": evidence or {}}, allow_nan=False)
        except (TypeError, ValueError) as exc:
            raise ValueError("parameters and evidence must contain finite JSON values") from exc

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
            evidence=dict(evidence or {}),
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

    def _validate_oos_promotion_evidence(
        self,
        strategy_id: str,
        info: dict[str, Any],
    ) -> str:
        """Require a present, self-consistent artifact before OOS_VALIDATED.

        Artifact paths are relative to the registry directory and must remain
        inside it after symlink resolution. The recorded metrics are checked
        against the artifact so stale registry metadata cannot be promoted by
        attaching a different JSON file.
        """
        evidence = info.get("evidence")
        if not isinstance(evidence, dict):
            raise ValueError("OOS_VALIDATED requires an evidence object")
        artifact_name = evidence.get("artifact")
        if not isinstance(artifact_name, str) or not artifact_name.strip():
            raise ValueError("OOS_VALIDATED requires evidence.artifact")

        relative_path = Path(artifact_name)
        if relative_path.is_absolute() or ".." in relative_path.parts:
            raise ValueError("evidence.artifact must be a relative path inside the registry directory")

        evidence_root = self.path.parent.resolve()
        try:
            artifact_path = (evidence_root / relative_path).resolve(strict=True)
            artifact_path.relative_to(evidence_root)
        except (OSError, ValueError) as exc:
            raise ValueError("OOS validation artifact is missing or outside the registry directory") from exc
        if not artifact_path.is_file():
            raise ValueError("OOS validation artifact must be a regular file")
        if artifact_path.stat().st_size > 10 * 1024 * 1024:
            raise ValueError("OOS validation artifact exceeds the 10 MiB safety limit")

        try:
            artifact_bytes = artifact_path.read_bytes()
            artifact = json.loads(artifact_bytes.decode("utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise ValueError("OOS validation artifact is unreadable or invalid JSON") from exc
        artifact_digest = hashlib.sha256(artifact_bytes).hexdigest()
        recorded_digest = evidence.get("artifact_sha256")
        if recorded_digest is not None and recorded_digest != artifact_digest:
            raise ValueError("OOS validation artifact changed after its evidence hash was recorded")
        if not isinstance(artifact, dict):
            raise ValueError("OOS validation artifact must be a JSON object")

        artifact_strategy = artifact.get("strategy_id", artifact.get("strategy"))
        if not isinstance(artifact_strategy, str) or artifact_strategy.removeprefix("strategy_") != strategy_id:
            raise ValueError("OOS validation artifact is missing or belongs to a different strategy")
        artifact_source_hash = artifact.get("strategy_source_hash")
        current_source_hash = info.get("source_hash")
        if (
            not isinstance(current_source_hash, str)
            or len(current_source_hash) != 64
            or not isinstance(artifact_source_hash, str)
            or artifact_source_hash != current_source_hash
        ):
            raise ValueError("OOS validation artifact source hash does not match this immutable strategy version")

        assessment = evaluate_oos_metrics(artifact)
        if not assessment["eligible"]:
            raise ValueError("OOS validation evidence is insufficient: " + "; ".join(assessment["reasons"]))
        if artifact.get("promotion_status") != PROMOTION_ELIGIBLE:
            raise ValueError(
                "OOS validation artifact does not declare PROMOTION_ELIGIBLE; "
                "RESEARCH ONLY evidence cannot be promoted"
            )

        recorded_status = evidence.get("promotion_status")
        if recorded_status != PROMOTION_ELIGIBLE:
            raise ValueError("registry evidence must affirm PROMOTION_ELIGIBLE")
        recorded_sha = evidence.get("git_sha")
        artifact_sha = artifact.get("git_sha")
        def is_git_sha(value: Any) -> bool:
            return (
                isinstance(value, str)
                and len(value) in {40, 64}
                and all(char in "0123456789abcdefABCDEF" for char in value)
            )

        if not is_git_sha(recorded_sha) or recorded_sha != artifact_sha:
            raise ValueError("registry evidence git_sha must match the cited artifact")

        parameters = info.get("parameters") or {}
        oos = artifact.get("optimized_oos") or {}
        metric_pairs = (
            ("OOS_TRADE_COUNT", "trade_count", "total_trades"),
            ("OOS_PROFIT_FACTOR", "profit_factor", None),
            ("OOS_EXPECTANCY_PER_TRADE", "expectancy", None),
        )
        for parameter_key, artifact_key, fallback_key in metric_pairs:
            if parameter_key not in parameters:
                continue
            artifact_value = oos.get(artifact_key)
            if artifact_value is None and fallback_key:
                artifact_value = oos.get(fallback_key)
            if artifact_value is None:
                raise ValueError(f"artifact is missing metric required to verify {parameter_key}")
            try:
                recorded_value = float(parameters[parameter_key])
                actual_value = float(artifact_value)
            except (TypeError, ValueError, OverflowError) as exc:
                raise ValueError(f"registry metric {parameter_key} is invalid") from exc
            if not (math.isfinite(recorded_value) and math.isfinite(actual_value)) or not math.isclose(
                recorded_value, actual_value, rel_tol=1e-6, abs_tol=1e-8
            ):
                raise ValueError(f"registry metric {parameter_key} does not match the cited artifact")

        if "walk_forward_failures" in evidence:
            try:
                recorded_failures = int(evidence["walk_forward_failures"])
            except (TypeError, ValueError, OverflowError) as exc:
                raise ValueError("registry walk_forward_failures metadata is invalid") from exc
            if recorded_failures != assessment["failed_windows"]:
                raise ValueError("registry walk-forward failure count does not match the cited artifact")

        return artifact_digest

    @_serialized_mutation
    def promote(
        self,
        strategy_id: str,
        version: str,
        new_status: str,
        actor: str = "operator",
        reason: str = "",
    ) -> StrategyVersion:
        """Explicitly promotes a strategy version according to the lifecycle state machine."""
        if not isinstance(strategy_id, str) or not strategy_id.strip():
            raise ValueError("strategy_id must be a non-empty string")
        if not isinstance(version, str) or not version.strip():
            raise ValueError("version must be a non-empty string")
        if not isinstance(new_status, str) or new_status not in VALID_STATUSES:
            raise ValueError(f"Invalid status '{new_status}'. Valid statuses: {VALID_STATUSES}")
        if not isinstance(actor, str) or not isinstance(reason, str):
            raise ValueError("actor and reason must be strings")

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

        # Re-check the evidence on every validation/approval/execution promotion,
        # including idempotent re-promotions of legacy registry records.
        if new_status in {"OOS_VALIDATED", "APPROVED", "ACTIVE"}:
            artifact_digest = self._validate_oos_promotion_evidence(strategy_id, current_info)
            current_info.setdefault("evidence", {}).setdefault("artifact_sha256", artifact_digest)

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
