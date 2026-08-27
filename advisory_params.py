"""
advisory_params.py — Runtime parameter overlay system for AI-Universe advisory modifications.

Maintains dynamic strategy parameter overrides on top of config_strategy.py defaults.
Features:
- Thread-safe runtime access: get_param(strategy, name, default).
- State persistence: advisory_params_state.json with associated decision_id and applied timestamp.
- Atomic file writes (.tmp then replace).
- Full rollback capability: rollback(decision_id).
- Safe reload on bot restart.
"""

import datetime
import json
import os
import threading
from typing import Any, Dict, List, Optional

import config_strategy
from logger import get_logger

logger = get_logger("advisory_params")

ADVISORY_PARAMS_FILE = os.getenv("ADVISORY_PARAMS_FILE", "advisory_params_state.json")


class AdvisoryParameterOverlay:
    """
    Manages live strategy parameter overrides applied through validated AI-Universe advisories.
    """

    def __init__(self, state_file: Optional[str] = None) -> None:
        self.state_file = state_file or ADVISORY_PARAMS_FILE
        self._lock = threading.RLock()
        self._overrides: Dict[str, Dict[str, Any]] = {}  # { "strategy_name": { "param_name": value } }
        self._history: List[Dict[str, Any]] = []         # History of applied batches
        self._last_applied_time: Optional[datetime.datetime] = None
        self._load_state()

    def _load_state(self) -> None:
        """Loads state from disk on startup."""
        with self._lock:
            if not os.path.exists(self.state_file):
                self._overrides = {}
                self._history = []
                self._last_applied_time = None
                return

            try:
                with open(self.state_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    self._overrides = data.get("overrides", {})
                    self._history = data.get("history", [])
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

    def _save_state(self) -> bool:
        """Persists parameter overlay state to disk atomically."""
        with self._lock:
            data = {
                "last_applied_timestamp": self._last_applied_time.isoformat() + "Z" if self._last_applied_time else None,
                "overrides": self._overrides,
                "history": self._history,
                "updated_at": datetime.datetime.utcnow().isoformat() + "Z"
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

    def get_current_params(self, strategy: Optional[str] = None) -> Dict[str, Any]:
        """
        Constructs a snapshot of all active parameters (defaults + active overrides).
        """
        strat_key = (strategy or "aggressive_scalper").strip().lower()
        params: Dict[str, Any] = {}

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

    def apply_changes(self, decision_id: str, changes: List[Dict[str, Any]]) -> bool:
        """
        Applies a validated list of parameter changes to the overlay and persists state.
        """
        if not changes:
            return False

        now = datetime.datetime.utcnow()
        with self._lock:
            batch_record = {
                "decision_id": decision_id,
                "timestamp": now.isoformat() + "Z",
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
            self._last_applied_time = now
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
                "timestamp": datetime.datetime.utcnow().isoformat() + "Z",
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
                "timestamp": datetime.datetime.utcnow().isoformat() + "Z",
                "reason": reason
            })
            self._save_state()
            logger.info(f"[ADVISORY_PARAMS] Reset all strategy parameter overlays to clean baseline defaults ({reason}).")

    def get_state(self) -> Dict[str, Any]:
        """Returns the full runtime overlay state."""
        with self._lock:
            return {
                "last_applied_timestamp": self._last_applied_time.isoformat() + "Z" if self._last_applied_time else None,
                "active_overrides": self._overrides,
                "history_count": len(self._history),
                "history": self._history[-20:]  # Return last 20 events
            }


# Singleton instance
_advisory_overlay: Optional[AdvisoryParameterOverlay] = None
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
