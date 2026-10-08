"""
autonomy/ecosystem_state.py — Full Ecosystem State Machine & Transition Controller.

Manages the global operational lifecycle across six states:
1. UNKNOWN: Startup or missing independent subsystem-health verification; no all-clear is implied.
2. FULL_AUTONOMY: All engines healthy, live/testnet trading active (must be explicitly verified).
3. DEGRADED: Partial peripheral outage (e.g. AI-Universe offline), trading with clean baseline defaults.
4. PROTECTED: Warning risk corridor reached (e.g. Drawdown 8-12%), sizing attenuated.
5. DEFENSIVE: Crisis/high-vol regime, minimal trading activity, strict capital preservation.
6. HALTED: Complete system lockout, zero orders, awaiting human operator intervention.
"""

import datetime
import itertools
import threading
import time
from dataclasses import asdict, dataclass
from typing import Any

from logger import get_logger

logger = get_logger("ecosystem_state")


@dataclass
class StateTransitionRecord:
    transition_id: str
    from_state: str
    to_state: str
    reason: str
    timestamp: str
    operator: str = "SYSTEM_AUTOMATIC"


AUTOMATIC_OPERATOR = "SYSTEM_AUTOMATIC"
_TRANSITION_SEQUENCE = itertools.count(1)


def _utc_iso(ts: float) -> str:
    """Render an epoch timestamp as an explicit UTC ISO-8601 string ending in ``Z``."""
    return datetime.datetime.fromtimestamp(ts, tz=datetime.timezone.utc).isoformat().replace("+00:00", "Z")


def _is_named_operator(operator: object) -> bool:
    """True only for a non-empty operator identity other than the automatic system actor."""
    return (
        isinstance(operator, str)
        and bool(operator.strip())
        and operator.strip().upper() != AUTOMATIC_OPERATOR
    )


class EcosystemStateMachine:
    """
    State machine governing global operational risk postures and lifecycle transitions.
    """

    STATES = ["UNKNOWN", "FULL_AUTONOMY", "DEGRADED", "PROTECTED", "DEFENSIVE", "HALTED"]

    def __init__(self, initial_state: str = "UNKNOWN"):
        self.current_state = initial_state if initial_state in self.STATES else "UNKNOWN"
        self.transition_history: list[StateTransitionRecord] = []
        self.last_transition_time = time.time()
        self._lock = threading.RLock()

    def transition_to(self, new_state: str, reason: str, operator: str = AUTOMATIC_OPERATOR) -> bool:
        """Transitions ecosystem to a new state and logs structured audit entry.

        HALTED is latched: automatic actors (``SYSTEM_AUTOMATIC``, blank or non-string
        operators) can enter it but can never leave it. Only a named human operator may
        release the lockout, and the release is recorded in the transition history.
        """
        if new_state not in self.STATES:
            logger.error(f"[ECOSYSTEM_STATE] Invalid state: {new_state}")
            return False

        with self._lock:
            if new_state == self.current_state:
                return True

            if self.current_state == "HALTED" and not _is_named_operator(operator):
                logger.warning(
                    f"[ECOSYSTEM_STATE] Refused automatic exit from HALTED -> {new_state} "
                    f"(operator={operator!r}); a named operator must release the lockout"
                )
                return False

            old_state = self.current_state
            now = time.time()
            self.current_state = new_state
            self.last_transition_time = now

            rec = StateTransitionRecord(
                transition_id=f"TRANS_{int(now * 1000)}_{next(_TRANSITION_SEQUENCE)}",
                from_state=old_state,
                to_state=new_state,
                reason=str(reason),
                timestamp=_utc_iso(now),
                operator=operator.strip() if isinstance(operator, str) and operator.strip() else AUTOMATIC_OPERATOR,
            )
            self.transition_history.append(rec)
        logger.info(f"[ECOSYSTEM_STATE] 🔄 TRANSITION: {old_state} -> {new_state} | Reason: {reason}")
        return True

    def get_state_summary(self) -> dict[str, Any]:
        """Returns snapshot of current state and recent transition history."""
        return {
            "current_state": self.current_state,
            "last_transition_time": _utc_iso(self.last_transition_time),
            "recent_transitions": [asdict(t) for t in self.transition_history[-10:]]
        }
