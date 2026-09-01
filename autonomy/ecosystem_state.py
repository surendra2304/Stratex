"""
autonomy/ecosystem_state.py — Full Ecosystem State Machine & Transition Controller.

Manages the global operational lifecycle across 5 distinct states:
1. FULL_AUTONOMY: All engines healthy, live/testnet trading active.
2. DEGRADED: Partial peripheral outage (e.g. AI-Universe offline), trading with clean baseline defaults.
3. PROTECTED: Warning risk corridor reached (e.g. Drawdown 8-12%), sizing attenuated.
4. DEFENSIVE: Crisis/high-vol regime, minimal trading activity, strict capital preservation.
5. HALTED: Complete system lockout, zero orders, awaiting human operator intervention.
"""

import datetime
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


class EcosystemStateMachine:
    """
    State machine governing global operational risk postures and lifecycle transitions.
    """

    STATES = ["FULL_AUTONOMY", "DEGRADED", "PROTECTED", "DEFENSIVE", "HALTED"]

    def __init__(self, initial_state: str = "FULL_AUTONOMY"):
        self.current_state = initial_state if initial_state in self.STATES else "FULL_AUTONOMY"
        self.transition_history: list[StateTransitionRecord] = []
        self.last_transition_time = time.time()

    def transition_to(self, new_state: str, reason: str, operator: str = "SYSTEM_AUTOMATIC") -> bool:
        """Transitions ecosystem to a new state and logs structured audit entry."""
        if new_state not in self.STATES:
            logger.error(f"[ECOSYSTEM_STATE] Invalid state: {new_state}")
            return False

        if new_state == self.current_state:
            return True

        old_state = self.current_state
        self.current_state = new_state
        self.last_transition_time = time.time()

        rec = StateTransitionRecord(
            transition_id=f"TRANS_{int(time.time()*1000)}",
            from_state=old_state,
            to_state=new_state,
            reason=reason,
            timestamp=datetime.datetime.utcnow().isoformat() + "Z",
            operator=operator
        )
        self.transition_history.append(rec)
        logger.info(f"[ECOSYSTEM_STATE] 🔄 TRANSITION: {old_state} -> {new_state} | Reason: {reason}")
        return True

    def get_state_summary(self) -> dict[str, Any]:
        """Returns snapshot of current state and recent transition history."""
        return {
            "current_state": self.current_state,
            "last_transition_time": datetime.datetime.fromtimestamp(self.last_transition_time).isoformat() + "Z",
            "recent_transitions": [asdict(t) for t in self.transition_history[-10:]]
        }
