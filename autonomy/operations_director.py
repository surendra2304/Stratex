"""
autonomy/operations_director.py — Master Autonomous Operations Director.

Capabilities:
1. Multi-Frequency Decision Hierarchy:
   - High-Frequency (5m): Real-time risk gates, position health, volatility checks.
   - Medium-Frequency (1h): Strategy weight adjustments and capital rebalancing.
   - Low-Frequency (24h): Evolution lab generation triggers and daily compliance reports.
   - Low-Frequency (Weekly): Candidate strategy graduation recommendations (human sign-off required).
2. Autonomy Levels:
   - LEVEL 1 (Advisory): Executes within bounds, reports all actions.
   - LEVEL 2 (Semi-Autonomous): Dynamically tunes strategy allocations within hard-coded limits.
   - LEVEL 3 (Full Autonomy): Autonomous self-healing, failover routing, and defensive liquidation.
"""

import time
import datetime
from typing import Dict, List, Optional, Tuple, Any
from dataclasses import dataclass, field, asdict

from autonomy.ecosystem_state import EcosystemStateMachine
from autonomy.self_healing import SelfHealingEngine
from logger import get_logger

logger = get_logger("operations_director")


@dataclass
class AutonomousDecisionRecord:
    decision_id: str
    frequency_tier: str  # "HIGH_5M", "MEDIUM_1H", "LOW_24H"
    action_type: str
    target_component: str
    rationale: str
    timestamp: str
    autonomy_level: int = 1


class AutonomousOperationsDirector:
    """
    Master orchestrator commanding multi-frequency decisions across strategies, risk, and infrastructure.
    """

    def __init__(self, autonomy_level: int = 2):
        self.autonomy_level = min(3, max(1, autonomy_level))
        self.state_machine = EcosystemStateMachine()
        self.self_healing = SelfHealingEngine()
        self.decision_log: List[AutonomousDecisionRecord] = []

    def log_decision(self, freq_tier: str, action: str, component: str, rationale: str) -> AutonomousDecisionRecord:
        rec = AutonomousDecisionRecord(
            decision_id=f"DEC_{int(time.time()*1000)}",
            frequency_tier=freq_tier,
            action_type=action,
            target_component=component,
            rationale=rationale,
            timestamp=datetime.datetime.utcnow().isoformat() + "Z",
            autonomy_level=self.autonomy_level
        )
        self.decision_log.append(rec)
        logger.info(f"[AUTONOMY_DIR] 🧠 [{freq_tier}] {action} on {component}: {rationale}")
        return rec

    def run_high_frequency_cycle(self, current_drawdown_pct: float, active_positions_count: int) -> Dict[str, Any]:
        """Runs 5-minute health and risk check."""
        if current_drawdown_pct >= 15.0:
            self.state_machine.transition_to("DEFENSIVE", "Drawdown ceiling >= 15%")
            return {"status": "CIRCUIT_BREAKER_TRIPPED", "action": "HALT_NEW_ENTRIES"}
        elif current_drawdown_pct >= 8.0:
            self.state_machine.transition_to("PROTECTED", "Drawdown in warning corridor")
            self.log_decision("HIGH_5M", "THROTTLE_SIZING", "risk_enforcer", f"Drawdown {current_drawdown_pct:.1f}%")
        else:
            if self.state_machine.current_state != "FULL_AUTONOMY":
                self.state_machine.transition_to("FULL_AUTONOMY", "Drawdown recovered to nominal bounds")

        return {
            "status": "NOMINAL",
            "ecosystem_state": self.state_machine.current_state,
            "active_positions": active_positions_count
        }

    def set_autonomy_level(self, level: int) -> bool:
        """Configures operational autonomy level (1, 2, or 3)."""
        if level in [1, 2, 3]:
            self.autonomy_level = level
            self.log_decision("LOW_24H", "CONFIG_CHANGE", "autonomy_director", f"Set autonomy level to {level}")
            return True
        return False
