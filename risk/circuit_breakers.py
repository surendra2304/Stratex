"""
risk/circuit_breakers.py — Multi-Pillar Systemic Risk Circuit Breakers.

Circuit Breakers:
1. Volatility Circuit Breaker: 24h realized volatility > 4σ from 30-day mean = halt 1 hour.
2. Correlation Breakdown Breaker: Cross-strategy correlation drops suddenly below 0.20 = reduce exposure (diversification failure).
3. Execution Quality Breaker: Realized slippage > 3x normal for 3 consecutive orders = halt and investigate.
4. API Latency Breaker: Exchange API response latency > 2.0s median = reduce order frequency / throttle.
"""

import time
import datetime
import numpy as np
from typing import Dict, List, Optional, Tuple, Any
from dataclasses import dataclass, field, asdict
from logger import get_logger

logger = get_logger("circuit_breakers")


@dataclass
class CircuitBreakerStatus:
    name: str
    is_tripped: bool
    tripped_at: Optional[float] = None
    reset_at: Optional[float] = None
    reason: str = ""
    severity: str = "HIGH"  # "CRITICAL", "HIGH", "MEDIUM"


class CircuitBreakerEngine:
    """
    Evaluates market conditions, execution telemetry, and latency against systemic circuit breakers.
    """

    def __init__(self):
        self.breakers: Dict[str, CircuitBreakerStatus] = {
            "volatility": CircuitBreakerStatus(name="volatility", is_tripped=False),
            "correlation_breakdown": CircuitBreakerStatus(name="correlation_breakdown", is_tripped=False),
            "execution_quality": CircuitBreakerStatus(name="execution_quality", is_tripped=False),
            "api_latency": CircuitBreakerStatus(name="api_latency", is_tripped=False)
        }
        self.consecutive_slippage_breaches = 0
        self.recent_latencies: List[float] = []

    def check_volatility_circuit_breaker(
        self,
        current_24h_vol: float,
        historical_vols: List[float]
    ) -> bool:
        """Checks if realized volatility is > 4 sigma above baseline."""
        now = time.time()
        # Check if currently cooling down
        if self.breakers["volatility"].is_tripped:
            if now < (self.breakers["volatility"].reset_at or 0):
                return True
            else:
                self.breakers["volatility"].is_tripped = False
                logger.info("[CIRCUIT_BREAKER] 🟢 Volatility circuit breaker cooled down and reset.")

        if len(historical_vols) < 15:
            return False

        mean_vol = float(np.mean(historical_vols))
        std_vol = float(np.std(historical_vols)) or 0.01
        z_score = (current_24h_vol - mean_vol) / std_vol

        if z_score >= 4.0:
            self.breakers["volatility"].is_tripped = True
            self.breakers["volatility"].tripped_at = now
            self.breakers["volatility"].reset_at = now + 3600  # Halt for 1 hour
            self.breakers["volatility"].reason = f"24h realized vol is {z_score:.1f}σ above mean (halted 1h)"
            logger.warning(f"[CIRCUIT_BREAKER] 🚨 VOLATILITY BREAKER TRIPPED: {self.breakers['volatility'].reason}")
            return True

        return False

    def check_correlation_breakdown(self, avg_strategy_corr: float) -> bool:
        """Checks if portfolio diversification broke down (avg cross-strategy correlation < 0.20 suddenly)."""
        if avg_strategy_corr < 0.20:
            self.breakers["correlation_breakdown"].is_tripped = True
            self.breakers["correlation_breakdown"].reason = f"Cross-strategy correlation dropped to {avg_strategy_corr:.2f} (< 0.20)"
            return True
        else:
            self.breakers["correlation_breakdown"].is_tripped = False
            return False

    def record_order_execution_slippage(self, realized_slippage_bps: float, normal_slippage_bps: float = 5.0) -> bool:
        """Checks if slippage > 3x normal for 3 consecutive orders."""
        if realized_slippage_bps > (3.0 * normal_slippage_bps):
            self.consecutive_slippage_breaches += 1
            if self.consecutive_slippage_breaches >= 3:
                self.breakers["execution_quality"].is_tripped = True
                self.breakers["execution_quality"].reason = f"Excessive slippage (> {3*normal_slippage_bps} bps) for 3 consecutive orders"
                logger.critical(f"[CIRCUIT_BREAKER] 🚨 EXECUTION QUALITY BREAKER TRIPPED: {self.breakers['execution_quality'].reason}")
                return True
        else:
            self.consecutive_slippage_breaches = 0
            self.breakers["execution_quality"].is_tripped = False
        return self.breakers["execution_quality"].is_tripped

    def record_api_latency(self, latency_seconds: float) -> bool:
        """Checks if median API response time > 2.0s."""
        self.recent_latencies.append(latency_seconds)
        if len(self.recent_latencies) > 20:
            self.recent_latencies.pop(0)

        median_lat = float(np.median(self.recent_latencies)) if self.recent_latencies else 0.0
        if median_lat > 2.0:
            self.breakers["api_latency"].is_tripped = True
            self.breakers["api_latency"].reason = f"Median API latency {median_lat:.2f}s > 2.0s (reducing order frequency)"
            return True
        else:
            self.breakers["api_latency"].is_tripped = False
            return False

    def get_status_summary(self) -> Dict[str, Any]:
        """Returns snapshot of all circuit breakers."""
        any_tripped = any(b.is_tripped for b in self.breakers.values())
        return {
            "any_circuit_breaker_active": any_tripped,
            "breakers": {k: asdict(v) for k, v in self.breakers.items()}
        }
