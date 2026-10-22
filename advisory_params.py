"""
advisory_params.py — Runtime parameter overlay system for AI-Universe advisory modifications.

Maintains dynamic strategy parameter overrides on top of config_strategy.py defaults.
Features:
- Thread-safe runtime access: get_param(strategy, name, default).
- State persistence: advisory_params_state.json with associated decision_id and applied timestamp.
- Atomic, cross-process locked writes (atomic_io); corrupt state is quarantined,
  never silently trusted, and a failed save never leaves an unpersisted change live.
- Full rollback capability: rollback(decision_id).
- Safe reload on bot restart.
- Explicit authorization requirement: separation of recommendation staging from application.
"""

import copy
import datetime
import os
import threading
import uuid
from typing import Any

import config_strategy
from atomic_io import atomic_write_json, load_json_state, locked_path, quarantine_file
from audit.audit_manager import AuditWriteError, get_audit_manager, get_idempotency_store, persisted_hash
from logger import get_logger

logger = get_logger("advisory_params")

ADVISORY_PARAMS_FILE = os.getenv("ADVISORY_PARAMS_FILE", "advisory_params_state.json")
# Applied-batch history kept on disk (rollback can only target batches still in it).
HISTORY_LIMIT = 1000


def _validate_overlay_state(data: dict[str, Any]) -> list[str]:
    """Structural problems that make a persisted overlay untrustworthy."""
    problems: list[str] = []
    overrides = data.get("overrides", {})
    if not isinstance(overrides, dict):
        problems.append(f"overrides is {type(overrides).__name__}, expected object")
    else:
        for strategy, params in overrides.items():
            if not isinstance(params, dict):
                problems.append(f"overrides[{strategy!r}] is {type(params).__name__}, expected object")
    history = data.get("history", [])
    if not isinstance(history, list) or not all(isinstance(item, dict) for item in history):
        problems.append("history is not a list of objects")
    pending = data.get("pending_recommendations", {})
    if not isinstance(pending, dict) or not all(isinstance(item, dict) for item in pending.values()):
        problems.append("pending_recommendations is not an object of objects")
    stamp = data.get("last_applied_timestamp")
    if stamp is not None:
        if not isinstance(stamp, str):
            problems.append("last_applied_timestamp is not a string")
        else:
            try:
                datetime.datetime.fromisoformat(stamp.replace("Z", "+00:00"))
            except ValueError:
                problems.append(f"last_applied_timestamp {stamp!r} is not ISO-8601")
    return problems


class AdvisoryParameterOverlay:
    """
    Manages live strategy parameter overrides applied through validated AI-Universe advisories.
    Separates advisory recommendation staging from explicit authorized application.
    """

    def __init__(self, state_file: str | None = None) -> None:
        self.state_file = state_file or ADVISORY_PARAMS_FILE
        self._lock = threading.RLock()
        self._overrides: dict[str, dict[str, Any]] = {}  # { "strategy_name": { "param_name": value } }
        self._history: list[dict[str, Any]] = []         # History of applied batches
        self._pending_recommendations: dict[str, dict[str, Any]] = {} # Staged bounded recommendations
        self._last_applied_time: datetime.datetime | None = None
        self._max_age_hours = self._resolve_max_age_hours()
        self._stale_logged = False
        self.state_status = "missing"
        self.quarantined_to: str | None = None
        self._load_state()

    @staticmethod
    def _resolve_max_age_hours() -> float:
        """Maximum age of AI-suggested parameter overrides before they stop
        steering trades. AI-Universe advisories must be re-validated within
        this window or the overlay silently degrades to strategy defaults.
        Set STRATEX_ADVISORY_MAX_AGE_HOURS<=0 to disable enforcement."""
        raw = os.getenv("STRATEX_ADVISORY_MAX_AGE_HOURS", "72").strip()
        try:
            return float(raw)
        except ValueError:
            logger.warning(f"[ADVISORY_PARAMS] Invalid STRATEX_ADVISORY_MAX_AGE_HOURS={raw!r}; using 72h")
            return 72.0

    def _overlay_expired(self) -> bool:
        """True when active overrides are older than the staleness budget.

        Fail-safe conventions:
        - TTL disabled (<=0) -> never expired.
        - No overrides -> not expired (nothing to serve).
        - Overrides present but no provenance timestamp -> expired (we cannot
          prove they are fresh, so they must not steer trades).
        """
        if self._max_age_hours <= 0:
            return False
        if not self._overrides:
            return False
        if self._last_applied_time is None:
            return True
        age = datetime.datetime.utcnow() - self._last_applied_time
        return age > datetime.timedelta(hours=self._max_age_hours)

    def _log_stale_once(self) -> None:
        if not self._stale_logged:
            logger.warning(
                f"[ADVISORY_PARAMS] Parameter overlay is older than {self._max_age_hours}h "
                f"(last applied: {self._last_applied_time}) — ignoring AI-suggested overrides "
                "and falling back to strategy defaults until the advisory re-validates them."
            )
            self._stale_logged = True

    def _load_state(self) -> None:
        """Loads state from disk on startup.

        A missing file means "no overrides". A file that is unreadable, not
        valid JSON, or structurally wrong is quarantined (preserved as
        ``<file>.corrupt-<UTC>``) and the overlay runs on strategy defaults —
        the same degradation the module uses for expired overrides — instead
        of trusting half-parsed values.
        """
        with self._lock:
            self._overrides = {}
            self._history = []
            self._pending_recommendations = {}
            self._last_applied_time = None
            with locked_path(self.state_file):
                result = load_json_state(self.state_file, expected_type=dict)
                self.state_status = result.status
                if result.status == "missing":
                    return
                if result.status == "corrupt":
                    self.quarantined_to = result.quarantined_to
                    logger.critical(
                        f"[ADVISORY_PARAMS] Overlay state {self.state_file} is corrupt ({result.error}); "
                        f"quarantined to {result.quarantined_to}. Running on strategy defaults."
                    )
                    return
                data = result.data
                problems = _validate_overlay_state(data)
                if problems:
                    reason = "; ".join(problems)
                    self.quarantined_to = quarantine_file(self.state_file, reason)
                    self.state_status = "corrupt"
                    logger.critical(
                        f"[ADVISORY_PARAMS] Overlay state {self.state_file} is structurally invalid ({reason}); "
                        f"quarantined to {self.quarantined_to}. Running on strategy defaults."
                    )
                    return
            self._overrides = data.get("overrides") or {}
            self._history = (data.get("history") or [])[-HISTORY_LIMIT:]
            self._pending_recommendations = data.get("pending_recommendations") or {}
            last_ts_str = data.get("last_applied_timestamp")
            if last_ts_str:
                parsed = datetime.datetime.fromisoformat(last_ts_str.replace("Z", "+00:00"))
                if parsed.tzinfo is not None:
                    parsed = parsed.astimezone(datetime.timezone.utc)
                self._last_applied_time = parsed.replace(tzinfo=None)
            logger.info(f"[ADVISORY_PARAMS] Loaded parameter overlay with {len(self._overrides)} strategy overrides.")

    def _save_state(self) -> bool:
        """Persists parameter overlay state atomically under a cross-process lock.

        Non-finite numbers are refused (``allow_nan=False``): strict JSON has no
        spelling for them and the file would be rejected on the next load.
        """
        with self._lock:
            data = {
                "last_applied_timestamp": self._last_applied_time.isoformat() + "Z" if self._last_applied_time else None,
                "overrides": self._overrides,
                "history": self._history[-HISTORY_LIMIT:],
                "pending_recommendations": self._pending_recommendations,
                "updated_at": datetime.datetime.now(datetime.timezone.utc).isoformat()
            }
            try:
                with locked_path(self.state_file):
                    atomic_write_json(self.state_file, data, indent=2, allow_nan=False)
                return True
            except (OSError, TypeError, ValueError) as e:
                logger.error(f"[ADVISORY_PARAMS] Atomic save failed for {self.state_file}: {e}")
                return False

    def _snapshot(self) -> tuple[Any, ...]:
        return (
            copy.deepcopy(self._overrides),
            copy.deepcopy(self._history),
            copy.deepcopy(self._pending_recommendations),
            self._last_applied_time,
        )

    def _restore(self, snapshot: tuple[Any, ...]) -> None:
        self._overrides, self._history, self._pending_recommendations, self._last_applied_time = snapshot

    def _append_history(self, record: dict[str, Any]) -> None:
        self._history.append(record)
        if len(self._history) > HISTORY_LIMIT:
            del self._history[: len(self._history) - HISTORY_LIMIT]

    def get_param(self, strategy: str, param_name: str, default: Any = None) -> Any:
        """
        Retrieves parameter value, checking the active overlay first, then fallback to default.
        """
        strat_key = strategy.strip().lower()
        param_key = param_name.strip().lower()

        with self._lock:
            # Stale overlay must never steer trades: fall through to defaults.
            if self._overlay_expired():
                self._log_stale_once()
                return default
            # 1. Strategy-specific override
            if strat_key in self._overrides and param_key in self._overrides[strat_key]:
                return self._overrides[strat_key][param_key]
            # 2. Global override
            if "global" in self._overrides and param_key in self._overrides["global"]:
                return self._overrides["global"][param_key]

        return default

    def get_current_params(self, strategy: str | None = None) -> dict[str, Any]:
        """
        Constructs a snapshot of all active parameters (defaults + active overrides).
        """
        strat_key = (strategy or "aggressive_scalper").strip().lower()
        params: dict[str, Any] = {}

        # Load from config_strategy
        if strat_key == "adx_ema":
            params.update({k.lower(): v for k, v in config_strategy.ADX_EMA_STRATEGY.items()})
        elif strat_key == "adx_ema_mtf":
            params.update({k.lower(): v for k, v in config_strategy.ADX_EMA_MTF_STRATEGY.items()})
        elif strat_key in config_strategy.PRODUCTION_STRATEGY_REGISTRY:
            reg = config_strategy.PRODUCTION_STRATEGY_REGISTRY[strat_key]
            params.update({k.lower(): v for k, v in reg.items()})

        # Apply global overrides — but never from an expired overlay.
        with self._lock:
            if not self._overlay_expired():
                if "global" in self._overrides:
                    params.update(self._overrides["global"])
                if strat_key in self._overrides:
                    params.update(self._overrides[strat_key])
            else:
                self._log_stale_once()

        return params

    def stage_recommendation(self, recommendation: dict[str, Any]) -> str:
        """
        Stages a validated bounded recommendation in the pending queue.
        Does NOT apply it to active parameter overrides.
        Returns the unique recommendation_id.
        """
        with self._lock:
            rec_id = str(recommendation.get("recommendation_id") or f"rec_{uuid.uuid4().hex[:8]}")
            staged = dict(recommendation)
            staged["recommendation_id"] = rec_id
            staged["staged_at"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
            staged["status"] = "PENDING_AUTHORIZATION"
            self._pending_recommendations[rec_id] = staged
            if not self._save_state():
                logger.error(f"[ADVISORY_PARAMS] Staged recommendation {rec_id} is held in memory only (save failed).")
            logger.info(f"[ADVISORY_PARAMS] Staged bounded recommendation {rec_id} for parameter '{staged.get('parameter')}'.")
            return rec_id

    def get_pending_recommendations(self) -> list[dict[str, Any]]:
        """Returns all staged bounded recommendations awaiting authorization."""
        with self._lock:
            return list(self._pending_recommendations.values())

    def apply_authorized_recommendation(
        self,
        recommendation_id: str,
        authorization_token: str,
        authorized_by: str = "FRIDAY",
        idempotency_key: str | None = None
    ) -> tuple[bool, str, dict[str, Any] | None]:
        """
        Separation of Advisory Generation and Application:
        Requires explicit authorization to apply a staged recommendation.
        Validates token, expiry, and idempotency.
        """
        if not authorization_token or str(authorization_token).strip() == "":
            return False, "Explicit authorization token is required.", None

        # Check idempotency
        recorded_key = False
        if idempotency_key:
            is_dup, cached = get_idempotency_store().check_and_record(
                idempotency_key=idempotency_key,
                request_type="PARAMETER_APPLICATION"
            )
            if is_dup:
                logger.info(f"[ADVISORY_PARAMS] Duplicate parameter application request for key '{idempotency_key}'.")
                return True, "Duplicate idempotent application handled.", cached.get("response") if cached else None
            recorded_key = True

        completed = False
        try:
            ok, message, payload = self._apply_authorized_locked(recommendation_id, authorization_token, authorized_by)
            if ok and recorded_key and idempotency_key:
                get_idempotency_store().complete_request(idempotency_key, payload or {})
                completed = True
            return ok, message, payload
        finally:
            # A failed attempt must not leave a PENDING key that makes the
            # operator's corrected retry look like an in-flight duplicate.
            if recorded_key and idempotency_key and not completed:
                get_idempotency_store().remove(idempotency_key)

    def _apply_authorized_locked(
        self,
        recommendation_id: str,
        authorization_token: str,
        authorized_by: str,
    ) -> tuple[bool, str, dict[str, Any] | None]:
        with self._lock:
            rec = self._pending_recommendations.get(recommendation_id)
            if not rec:
                return False, f"Recommendation '{recommendation_id}' not found in pending queue.", None

            # Check expiry
            now = datetime.datetime.now(datetime.timezone.utc)
            expiry_str = rec.get("expiry")
            if expiry_str:
                try:
                    exp_dt = datetime.datetime.fromisoformat(str(expiry_str).replace("Z", "+00:00"))
                except ValueError:
                    # Fail closed: an unparseable expiry cannot prove the
                    # recommendation is still valid.
                    logger.warning(f"Could not parse expiry '{expiry_str}' of {recommendation_id}; refusing to apply.")
                    return False, f"Recommendation '{recommendation_id}' has an unparseable expiry.", None
                if exp_dt.tzinfo is None:
                    exp_dt = exp_dt.replace(tzinfo=datetime.timezone.utc)
                if exp_dt < now:
                    del self._pending_recommendations[recommendation_id]
                    self._save_state()
                    get_audit_manager().record_event(
                        event_type="RECOMMENDATION_REJECTED",
                        actor=authorized_by,
                        details={"recommendation_id": recommendation_id, "reason": "EXPIRED_AT_APPLICATION"},
                        rationale=f"Staged recommendation expired at {expiry_str}",
                        status="EXPIRED"
                    )
                    return False, f"Recommendation '{recommendation_id}' expired before authorization.", None

            # Apply parameter modification
            param = rec.get("parameter")
            strategy = rec.get("strategy", "global")
            proposed_val = rec.get("proposed_value", rec.get("new_value"))
            current_val = rec.get("current_value")
            token_hint = authorization_token[:12] + "..." if len(authorization_token) > 12 else authorization_token

            change = {
                "strategy": strategy,
                "parameter": param,
                "current_value": current_val,
                "new_value": proposed_val,
                "reason": f"Authorized by {authorized_by} (token={authorization_token[:8]}...)"
            }

            # No audit, no change: the authorization must be on the hash chain
            # before the overlay is touched.
            try:
                get_audit_manager().record_event(
                    event_type="RECOMMENDATION_AUTHORIZED",
                    actor=authorized_by,
                    details={
                        "recommendation_id": recommendation_id,
                        "parameter": param,
                        "strategy": strategy,
                        "new_value": proposed_val,
                        "authorization_token": token_hint,
                    },
                    rationale="Explicit authorization received; applying parameter change",
                    status="AUTHORIZED",
                    required=True,
                )
            except AuditWriteError as exc:
                logger.critical(f"[ADVISORY_PARAMS] Refusing to apply {recommendation_id}: {exc}")
                return False, f"Audit trail unavailable; '{recommendation_id}' was not applied.", None

            applied = self.apply_changes(decision_id=recommendation_id, changes=[change])
            if not applied:
                return False, "Failed applying parameter change to overlay.", None

            # Record in audit trail
            audit_rec = get_audit_manager().record_event(
                event_type="RECOMMENDATION_APPLIED",
                actor=authorized_by,
                details={
                    "recommendation_id": recommendation_id,
                    "parameter": param,
                    "strategy": strategy,
                    "previous_value": current_val,
                    "new_value": proposed_val,
                    "authorization_token": token_hint
                },
                rationale=rec.get("evidence", "Explicitly authorized parameter change"),
                status="APPLIED"
            )

            # Clean from pending queue
            del self._pending_recommendations[recommendation_id]
            if not self._save_state():
                logger.error(f"[ADVISORY_PARAMS] Applied {recommendation_id} but could not persist its removal from the pending queue.")

            res_payload = {
                "recommendation_id": recommendation_id,
                "applied_change": change,
                "authorized_by": authorized_by,
                "status": "APPLIED",
                "audit_event": persisted_hash(audit_rec)
            }
            return True, f"Successfully applied recommendation '{recommendation_id}'.", res_payload

    def apply_changes(self, decision_id: str, changes: list[dict[str, Any]]) -> bool:
        """
        Applies a validated list of parameter changes to the overlay and persists state.
        """
        if not changes:
            return False

        now = datetime.datetime.now(datetime.timezone.utc)
        with self._lock:
            snapshot = self._snapshot()
            batch_record = {
                "decision_id": decision_id,
                "timestamp": now.isoformat(),
                "changes": []
            }

            for ch in changes:
                strat = str(ch.get("strategy", "global")).strip().lower()
                param = str(ch.get("parameter", "")).strip().lower()
                new_val = ch.get("new_value")
                old_val = ch.get("current_value")

                if strat not in self._overrides:
                    self._overrides[strat] = {}

                self._overrides[strat][param] = new_val
                batch_record["changes"].append({
                    "strategy": strat,
                    "parameter": param,
                    "previous_value": old_val,
                    "new_value": new_val,
                    "reason": ch.get("reason", "")
                })

            self._append_history(batch_record)
            self._last_applied_time = now.replace(tzinfo=None)
            if not self._save_state():
                # Fail closed: a change that is not on disk would silently
                # disappear on restart, so it must not steer trades now either.
                self._restore(snapshot)
                logger.critical(f"[ADVISORY_PARAMS] Decision {decision_id} NOT applied: overlay state could not be persisted.")
                return False
            self._stale_logged = False
            logger.info(f"[ADVISORY_PARAMS] Successfully applied decision {decision_id} ({len(changes)} parameter changes).")
            return True

    def rollback(self, decision_id: str) -> bool:
        """
        Rolls back all parameter changes applied under a specific decision_id.
        """
        with self._lock:
            target_batch = None
            for b in reversed(self._history):
                if b.get("decision_id") == decision_id:
                    target_batch = b
                    break

            if not target_batch:
                logger.warning(f"[ADVISORY_PARAMS] Rollback failed: decision_id '{decision_id}' not found in history.")
                return False

            # Revert parameters to previous values
            for ch in target_batch.get("changes", []):
                strat = ch["strategy"]
                param = ch["parameter"]
                prev_val = ch.get("previous_value")

                if prev_val is not None:
                    if strat in self._overrides:
                        self._overrides[strat][param] = prev_val
                else:
                    if strat in self._overrides and param in self._overrides[strat]:
                        del self._overrides[strat][param]
                        if not self._overrides[strat]:
                            del self._overrides[strat]

            # Mark in history
            self._append_history({
                "decision_id": f"ROLLBACK_{decision_id}",
                "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                "rolled_back_decision": decision_id,
                "reverted_changes": target_batch.get("changes", [])
            })
            if not self._save_state():
                # Reverting toward defaults is the safe direction, so the
                # in-memory rollback stays in force; the operator must know
                # it will not survive a restart.
                logger.critical(f"[ADVISORY_PARAMS] Rollback of {decision_id} is active in memory but was NOT persisted.")
            logger.info(f"[ADVISORY_PARAMS] Rolled back decision {decision_id} successfully.")
            return True

    def reset_to_defaults(self, reason: str = "CIRCUIT_BREAKER_RESET") -> None:
        """Clears all active overrides, reverting completely to config_strategy.py defaults."""
        with self._lock:
            self._overrides = {}
            self._append_history({
                "decision_id": f"RESET_{reason}",
                "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                "reason": reason
            })
            if not self._save_state():
                logger.critical(f"[ADVISORY_PARAMS] Reset to defaults ({reason}) is active in memory but was NOT persisted.")
            logger.info(f"[ADVISORY_PARAMS] Reset all strategy parameter overlays to clean baseline defaults ({reason}).")

    def get_state(self) -> dict[str, Any]:
        """Returns the full runtime overlay state."""
        with self._lock:
            expired = self._overlay_expired()
            age_hours = None
            if self._last_applied_time is not None:
                age_hours = round((datetime.datetime.utcnow() - self._last_applied_time).total_seconds() / 3600.0, 2)
            overlay_status = "EMPTY"
            if self._overrides:
                if self._max_age_hours <= 0:
                    overlay_status = "ACTIVE_TTL_DISABLED"
                elif expired:
                    overlay_status = "STALE_IGNORED"
                else:
                    overlay_status = "ACTIVE"
            return {
                "last_applied_timestamp": self._last_applied_time.isoformat() + "Z" if self._last_applied_time else None,
                "overlay_age_hours": age_hours,
                "max_age_hours": self._max_age_hours if self._max_age_hours > 0 else None,
                "overlay_status": overlay_status,
                "overrides_active": bool(self._overrides) and not expired,
                "active_overrides": self._overrides,
                "pending_recommendations_count": len(self._pending_recommendations),
                "pending_recommendations": list(self._pending_recommendations.values()),
                "history_count": len(self._history),
                "history": self._history[-20:]
            }


# Singleton instance
_advisory_overlay: AdvisoryParameterOverlay | None = None
_overlay_init_lock = threading.Lock()


def get_advisory_overlay() -> AdvisoryParameterOverlay:
    """Returns singleton instance of AdvisoryParameterOverlay."""
    global _advisory_overlay
    if _advisory_overlay is None:
        with _overlay_init_lock:
            if _advisory_overlay is None:
                _advisory_overlay = AdvisoryParameterOverlay()
    return _advisory_overlay


def get_param(strategy: str, param_name: str, default: Any = None) -> Any:
    """Module-level helper to query parameter overlay values."""
    return get_advisory_overlay().get_param(strategy, param_name, default)
