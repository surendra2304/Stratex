"""
autonomy/degradation_matrix.py — Graceful Ecosystem Degradation Matrix & Defense Policies.

Policies:
1. AI-Universe Down:
   - Continue trading using last validated parameters.
   - Flag advisory as DEGRADED.
   - Retry reconnection every 5 minutes.
2. Exchange Degraded:
   - Reduce all position sizes by 50%.
   - Widen protective stops by +20%.
   - Halt scalping strategies.
3. Dashboard Down:
   - Trading continues uninterrupted.
   - Queue telemetry and alerts locally for delivery upon recovery.
4. Monitoring Down:
   - Trade at 50% normal sizing (blind trading safety factor).
5. Complete System Degradation:
   - Flatten all positions.
   - Execute full emergency halt.
   - Alert operator via secondary backup channels.
"""

from typing import Dict, List, Optional, Tuple, Any
from dataclasses import dataclass, field, asdict
from logger import get_logger

logger = get_logger("degradation_matrix")


@dataclass
class SubsystemHealth:
    ai_universe_online: bool = True
    exchange_healthy: bool = True
    dashboard_online: bool = True
    monitoring_online: bool = True


class DegradationPolicyMatrix:
    """
    Evaluates subsystem health matrices and outputs defensive trading adjustments.
    """

    def evaluate_degradation_policy(self, health: SubsystemHealth) -> Dict[str, Any]:
        """Calculates defensive adjustments based on subsystem status."""
        position_size_multiplier = 1.0
        stop_loss_multiplier = 1.0
        halt_scalpers = False
        halt_all_entries = False
        emergency_flatten = False
        actions_taken = []

        # 1. Complete System Degradation
        if not health.exchange_healthy and not health.monitoring_online:
            emergency_flatten = True
            halt_all_entries = True
            position_size_multiplier = 0.0
            actions_taken.append("COMPLETE_SYSTEM_DEGRADATION: FLATTEN ALL POSITIONS AND FULL HALT")
            logger.critical("[DEGRADATION] 🚨 COMPLETE DEGRADATION TRIGGERED")
            return {
                "position_size_multiplier": 0.0,
                "stop_loss_multiplier": 1.0,
                "halt_scalpers": True,
                "halt_all_entries": True,
                "emergency_flatten": True,
                "actions": actions_taken
            }

        # 2. AI-Universe Down
        if not health.ai_universe_online:
            actions_taken.append("AI_UNIVERSE_DOWN: Trading on cached parameters; retrying advisory every 5m")
            logger.warning("[DEGRADATION] Advisory offline. Operating on last validated strategy parameters.")

        # 3. Exchange Degraded
        if not health.exchange_healthy:
            position_size_multiplier *= 0.50
            stop_loss_multiplier = 1.20  # Widen stops by +20%
            halt_scalpers = True
            actions_taken.append("EXCHANGE_DEGRADED: Sizing -50%, Stops +20%, Scalping halted")
            logger.warning("[DEGRADATION] Exchange degraded. Enforcing defensive parameter widening.")

        # 4. Monitoring Down
        if not health.monitoring_online:
            position_size_multiplier *= 0.50
            actions_taken.append("MONITORING_DOWN: Sizing reduced to 50% for blind trading risk control")
            logger.warning("[DEGRADATION] Monitoring service offline. Halving position sizing.")

        # 5. Dashboard Down
        if not health.dashboard_online:
            actions_taken.append("DASHBOARD_DOWN: Trading continues uninterrupted; alerts queued")

        return {
            "position_size_multiplier": round(position_size_multiplier, 2),
            "stop_loss_multiplier": round(stop_loss_multiplier, 2),
            "halt_scalpers": halt_scalpers,
            "halt_all_entries": halt_all_entries,
            "emergency_flatten": emergency_flatten,
            "actions": actions_taken
        }
