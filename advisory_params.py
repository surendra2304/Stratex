"""
advisory_params.py — Runtime parameter overlay system for AI-Universe advisory modifications.

Maintains dynamic strategy parameter overrides on top of config_strategy.py defaults.
Features:
- Thread-safe runtime access: get_param(strategy, name, default).
- State persistence: advisory_params_state.json with associated decision_id and applied timestamp.
- Atomic file writes (.tmp then replace).
- Full rollback capability: rollback(decision_id).
- Safe reload on bot restart.
- Explicit authorization requirement: separation of recommendation staging from application.
"""

import datetime
import json
import os
import threading
import uuid
from typing import Any

import config_strategy
from audit.audit_manager import get_audit_manager, get_idempotency_store
from logger import get_logger

logger = get_logger("advisory_params")

ADVISORY_PARAMS_FILE = os.getenv("ADVISORY_PARAMS_FILE", "advisory_params_state.json")


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
        self._load_state()

    def _load_state(self) -> None:
        """Loads state from disk on startup."""
        with self._lock:
            if not os.path.exists(self.state_file):
                self._overrides = {}
                self._history = []
                self._pending_recommendations = {}
                self._last_applied_time = None
                return

            try:
                with open(self.state_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    self._overrides = data.get("overrides", {})
                    self._history = data.get("history", [])
                    self._pending_recommendations = data.get("pending_recommendations", {})
                    last_ts_str = data.get("last_applied_timestamp")
                    if last_ts_str:
                        try:
                            self._last_applied_time = datetime.datetime.fromisoformat(last_ts_str.replace("Z", "+00:00")).replace(tzinfo=None)
                        except Exception:
                            self._last_applied_time = None
                logger.info(f"[ADVISORY_PARAMS] Loaded parameter overlay with {len(self._overrides)} strategy overrides.")
            except Exception as e:
                logger.error(f"[ADVISORY_PARAMS] Failed to load overlay state from {self.state_file}: {e}")
                self._overrides = {}
                self._pending_recommendations = {}

    def _save_state(self) -> bool:
        """Persists parameter overlay state to disk atomically."""
        with self._lock:
            data = {
                "last_applied_timestamp": self._last_applied_time.isoformat() + "Z" if self._last_applied_time else None,
                "overrides": self._overrides,
                "history": self._history,
                "pending_recommendations": self._pending_recommendations,
                "updated_at": datetime.datetime.now(datetime.timezone.utc).isoformat()
            }
            tmp_file = self.state_file + ".tmp"
            try:
                with open(tmp_file, "w", encoding="utf-8") as f:
                    json.dump(data, f, indent=2)
                os.replace(tmp_file, self.state_file)
                return True
            except Exception as e:
                logger.error(f"[ADVISORY_PARAMS] Atomic save failed for {self.state_file}: {e}")
                return False

    def get_param(self, strategy: str, param_name: str, default: Any = None) -> Any:
        """
        Retrieves parameter value, checking the active overlay first, then fallback to default.
        """
        strat_key = strategy.strip().lower()
        param_key = param_name.strip().lower()

        with self._lock:
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

        # Apply global overrides
        with self._lock:
            if "global" in self._overrides:
                params.update(self._overrides["global"])
            if strat_key in self._overrides:
                params.update(self._overrides[strat_key])

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
            self._save_state()
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
        if idempotency_key:
            is_dup, cached = get_idempotency_store().check_and_record(
                idempotency_key=idempotency_key,
                request_type="PARAMETER_APPLICATION"
            )
            if is_dup:
                logger.info(f"[ADVISORY_PARAMS] Duplicate parameter application request for key '{idempotency_key}'.")
                return True, "Duplicate idempotent application handled.", cached.get("response") if cached else None

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
                except Exception as e:
                    logger.warning(f"Could not parse expiry '{expiry_str}': {e}")

            # Apply parameter modification
            param = rec.get("parameter")
            strategy = rec.get("strategy", "global")
            proposed_val = rec.get("proposed_value", rec.get("new_value"))
            current_val = rec.get("current_value")

            change = {
                "strategy": strategy,
                "parameter": param,
                "current_value": current_val,
                "new_value": proposed_val,
                "reason": f"Authorized by {authorized_by} (token={authorization_token[:8]}...)"
            }

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
                    "authorization_token": authorization_token[:12] + "..." if len(authorization_token) > 12 else authorization_token
                },
                rationale=rec.get("evidence", "Explicitly authorized parameter change"),
                status="APPLIED"
            )

            # Clean from pending queue
            del self._pending_recommendations[recommendation_id]
            self._save_state()

            res_payload = {
                "recommendation_id": recommendation_id,
                "applied_change": change,
                "authorized_by": authorized_by,
                "status": "APPLIED",
                "audit_event": audit_rec.get("hash")
            }

            if idempotency_key:
                get_idempotency_store().complete_request(idempotency_key, res_payload)

            return True, f"Successfully applied recommendation '{recommendation_id}'.", res_payload

    def apply_changes(self, decision_id: str, changes: list[dict[str, Any]]) -> bool:
        """
        Applies a validated list of parameter changes to the overlay and persists state.
        """
        if not changes:
            return False

        now = datetime.datetime.now(datetime.timezone.utc)
        with self._lock:
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

            self._history.append(batch_record)
            self._last_applied_time = now.replace(tzinfo=None)
            self._save_state()
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
            self._history.append({
                "decision_id": f"ROLLBACK_{decision_id}",
                "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                "rolled_back_decision": decision_id,
                "reverted_changes": target_batch.get("changes", [])
            })
            self._save_state()
            logger.info(f"[ADVISORY_PARAMS] Rolled back decision {decision_id} successfully.")
            return True

    def reset_to_defaults(self, reason: str = "CIRCUIT_BREAKER_RESET") -> None:
        """Clears all active overrides, reverting completely to config_strategy.py defaults."""
        with self._lock:
            self._overrides = {}
            self._history.append({
                "decision_id": f"RESET_{reason}",
                "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                "reason": reason
            })
            self._save_state()
            logger.info(f"[ADVISORY_PARAMS] Reset all strategy parameter overlays to clean baseline defaults ({reason}).")

    def get_state(self) -> dict[str, Any]:
        """Returns the full runtime overlay state."""
        with self._lock:
            return {
                "last_applied_timestamp": self._last_applied_time.isoformat() + "Z" if self._last_applied_time else None,
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
