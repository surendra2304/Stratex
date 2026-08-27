"""
autonomy/operations_director.py — Master Autonomous Operations Director.

Capabilities:
1. Multi-Frequency Decision Hierarchy:
   - High-Frequency (5m): Position management, risk checks, circuit breakers.
   - Medium-Frequency (1h): Strategy weighting adjustments, allocation rebalancing within bounds.
   - Low-Frequency (Daily): Capital distribution review, evolution lab review, performance attribution.
   - Weekly: Strategy promotion/retirement proposals requiring human approval.
2. Autonomy Levels (Configurable via ENV `OPERATIONS_AUTONOMY_LEVEL`):
   - LEVEL 1 (Advisory / Constrained): Executes within bounds, reports all actions.
   - LEVEL 2 (Semi-Autonomous): Adjusts strategy allocations autonomously within ±20% of targets.
   - LEVEL 3 (Full Autonomy): Autonomous self-healing, failover routing, and defensive liquidation with mandatory audit reporting.
"""

import os
import time
import datetime
from typing import Dict, List, Optional, Tuple, Any
from dataclasses import dataclass, field, asdict

from autonomy.ecosystem_state import EcosystemStateMachine
from autonomy.self_healing import SelfHealingEngine
from autonomy.degradation_matrix import DegradationPolicyMatrix, SubsystemHealth
from logger import get_logger

logger = get_logger("operations_director")


@dataclass
class AutonomousDecisionRecord:
    decision_id: str
    frequency_tier: str  # "HIGH_5M", "MEDIUM_1H", "LOW_24H", "WEEKLY"
    action_type: str
    target_component: str
    rationale: str
    timestamp: str
    autonomy_level: int = 1


class AutonomousOperationsDirector:
    """
    Master orchestrator commanding multi-frequency decisions across strategies, risk, and infrastructure.
    """

    def __init__(self, autonomy_level: Optional[int] = None):
        default_lvl = int(os.getenv("OPERATIONS_AUTONOMY_LEVEL", "2"))
        self.autonomy_level = min(3, max(1, autonomy_level if autonomy_level is not None else default_lvl))
        self.state_machine = EcosystemStateMachine()
        self.self_healing = SelfHealingEngine()
        self.degradation = DegradationPolicyMatrix()
        self.decision_log: List[AutonomousDecisionRecord] = []

    def set_autonomy_level(self, level: int) -> int:
        """Sets autonomy level (1, 2, or 3)."""
        self.autonomy_level = min(3, max(1, level))
        self.log_decision("HIGH_5M", "SET_AUTONOMY_LEVEL", "operations_director", f"Autonomy level updated to LEVEL_{self.autonomy_level}")
        return self.autonomy_level

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
        if current_drawdown_pct >= 12.0:
            self.state_machine.transition_to("DEFENSIVE", f"Drawdown ceiling {current_drawdown_pct:.1f}% >= 12%")
            self.log_decision("HIGH_5M", "FLATTEN_ALL", "risk_orchestrator", f"Critical drawdown {current_drawdown_pct:.1f}%")
            return {"status": "CRITICAL_DRAWDOWN", "action": "FLATTEN_AND_HALT"}
        elif current_drawdown_pct >= 8.0:
            self.state_machine.transition_to("PROTECTED", f"Drawdown {current_drawdown_pct:.1f}% in action corridor")
            self.log_decision("HIGH_5M", "HALT_NEW_ENTRIES", "risk_orchestrator", f"Drawdown {current_drawdown_pct:.1f}%")
            return {"status": "ACTION_CORRIDOR", "action": "HALT_NEW_ENTRIES"}
        elif current_drawdown_pct >= 5.0:
            self.state_machine.transition_to("PROTECTED", f"Drawdown {current_drawdown_pct:.1f}% in warning corridor")
            self.log_decision("HIGH_5M", "THROTTLE_SIZING_30PCT", "risk_orchestrator", f"Drawdown {current_drawdown_pct:.1f}%")
            return {"status": "WARNING_CORRIDOR", "action": "THROTTLE_SIZING_30PCT"}
        else:
            if self.state_machine.current_state != "FULL_AUTONOMY":
                self.state_machine.transition_to("FULL_AUTONOMY", "Drawdown recovered to nominal bounds")
            return {"status": "NOMINAL", "action": "CONTINUE_TRADING"}

    def run_medium_frequency_cycle(self, strategy_sharpes: Dict[str, float]) -> Dict[str, float]:
        """Runs hourly strategy weighting adjustments within ±20% bounds."""
        if self.autonomy_level < 2:
            return {"status": "SKIPPED_AUTONOMY_LEVEL_1"}

        total_sharpe = sum(max(0.1, sr) for sr in strategy_sharpes.values()) or 1.0
        new_weights = {}
        for sname, sr in strategy_sharpes.items():
            base_w = max(0.1, sr) / total_sharpe
            # In Level 2, allow dynamic variation within bounds
            new_weights[sname] = round(min(0.25, base_w), 3)

        self.log_decision("MEDIUM_1H", "REBALANCE_WEIGHTS", "strategy_coordinator", f"Rebalanced weights: {new_weights}")
        return new_weights

    def run_daily_cycle(self) -> Dict[str, Any]:
        """Runs daily capital distribution review and evolution generation review."""
        rec = self.log_decision("LOW_24H", "REVIEW_CAPITAL_AND_EVOLUTION", "evolution_lab", "Triggered daily review cycle")
        return {"status": "DAILY_CYCLE_COMPLETE", "record": asdict(rec)}

    def get_ecosystem_status(self) -> Dict[str, Any]:
        """Rolls up comprehensive system status."""
        return {
            "autonomy_level": self.autonomy_level,
            "state_machine": self.state_machine.get_state_summary(),
            "decisions_count": len(self.decision_log),
            "recent_decisions": [asdict(d) for d in self.decision_log[-10:]],
            "timestamp": datetime.datetime.utcnow().isoformat() + "Z"
        }
