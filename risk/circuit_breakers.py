"""
risk/circuit_breakers.py — Multi-Pillar Systemic Risk Circuit Breakers.

Circuit Breakers:
1. Volatility Circuit Breaker: 24h realized volatility > 4σ from 30-day mean = halt 1 hour.
2. Correlation Breakdown Breaker: Cross-strategy correlation drops suddenly below 0.20 = reduce exposure (diversification failure).
3. Execution Quality Breaker: Realized slippage > 3x normal for 3 consecutive orders = halt and investigate.
4. API Latency Breaker: Exchange API response latency > 2.0s median = reduce order frequency / throttle.

Fail-closed numeric handling: every breaker compared raw floats, and ``NaN``
compares False against everything, so a NaN reading *reset* a tripped breaker
(the "else" branch ran): NaN slippage cleared three consecutive breaches, NaN
correlation untripped the correlation breaker, one NaN latency made the median
NaN and untripped the latency breaker, and NaN volatility never tripped. A
non-finite or impossible reading now counts as a breach.

This module used to be byte-for-byte duplicated as ``risk/live_enforcer.py``
(both files defined ``CircuitBreakerEngine`` *and* ``LiveRiskEnforcer``), so a
fix to one copy silently missed the other. ``LiveRiskEnforcer`` now lives only
in :mod:`risk.live_enforcer` and is re-exported here for compatibility.
"""

import math
import time
from dataclasses import asdict, dataclass
from typing import Any

import numpy as np

from logger import get_logger
from numeric_safety import finite_float, finite_values, positive_float

logger = get_logger("circuit_breakers")

MIN_VOL_HISTORY = 15
LATENCY_WINDOW = 20


@dataclass
class CircuitBreakerStatus:
    name: str
    is_tripped: bool
    tripped_at: float | None = None
    reset_at: float | None = None
    reason: str = ""
    severity: str = "HIGH"  # "CRITICAL", "HIGH", "MEDIUM"


class CircuitBreakerEngine:
    """
    Evaluates market conditions, execution telemetry, and latency against systemic circuit breakers.
    """

    def __init__(self):
        self.breakers: dict[str, CircuitBreakerStatus] = {
            "volatility": CircuitBreakerStatus(name="volatility", is_tripped=False),
            "correlation_breakdown": CircuitBreakerStatus(name="correlation_breakdown", is_tripped=False),
            "execution_quality": CircuitBreakerStatus(name="execution_quality", is_tripped=False),
            "api_latency": CircuitBreakerStatus(name="api_latency", is_tripped=False)
        }
        self.consecutive_slippage_breaches = 0
        self.recent_latencies: list[float] = []

    def _trip(self, name: str, reason: str, *, reset_after: float | None = None) -> None:
        now = time.time()
        breaker = self.breakers[name]
        breaker.is_tripped = True
        breaker.tripped_at = now
        breaker.reset_at = now + reset_after if reset_after is not None else None
        breaker.reason = reason

    def check_volatility_circuit_breaker(
        self,
        current_24h_vol: float,
        historical_vols: list[float]
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

        current = finite_float(current_24h_vol)
        if current is None or current < 0:
            self._trip("volatility", f"Unmeasurable 24h volatility reading {current_24h_vol!r} (halted 1h)",
                       reset_after=3600)
            logger.warning(f"[CIRCUIT_BREAKER] 🚨 VOLATILITY BREAKER TRIPPED: {self.breakers['volatility'].reason}")
            return True

        history = finite_values(historical_vols or [])
        if len(history) < MIN_VOL_HISTORY:
            return False

        mean_vol = float(np.mean(history))
        std_vol = float(np.std(history)) or 0.01
        z_score = (current - mean_vol) / std_vol

        if z_score >= 4.0:
            self._trip("volatility", f"24h realized vol is {z_score:.1f}σ above mean (halted 1h)", reset_after=3600)
            logger.warning(f"[CIRCUIT_BREAKER] 🚨 VOLATILITY BREAKER TRIPPED: {self.breakers['volatility'].reason}")
            return True
        return False

    def check_correlation_breakdown(self, avg_strategy_corr: float) -> bool:
        """Checks if portfolio diversification broke down (avg cross-strategy correlation < 0.20 suddenly)."""
        corr = finite_float(avg_strategy_corr)
        if corr is None or not -1.0 <= corr <= 1.0:
            self._trip("correlation_breakdown", f"Unmeasurable cross-strategy correlation {avg_strategy_corr!r}")
            return True
        if corr < 0.20:
            self._trip("correlation_breakdown", f"Cross-strategy correlation dropped to {corr:.2f} (< 0.20)")
            return True
        self.breakers["correlation_breakdown"].is_tripped = False
        return False

    def record_order_execution_slippage(self, realized_slippage_bps: float, normal_slippage_bps: float = 5.0) -> bool:
        """Checks if slippage > 3x normal for 3 consecutive orders.

        An unreadable slippage measurement counts as a breach; it never clears
        the consecutive-breach counter.
        """
        normal = positive_float(normal_slippage_bps)
        if normal is None:
            raise ValueError(f"normal_slippage_bps must be finite and > 0, got {normal_slippage_bps!r}")
        slippage = finite_float(realized_slippage_bps)
        if slippage is None or slippage > (3.0 * normal):
            self.consecutive_slippage_breaches += 1
            if self.consecutive_slippage_breaches >= 3:
                self._trip("execution_quality",
                           f"Excessive or unmeasurable slippage (> {3 * normal} bps) for 3 consecutive orders")
                self.breakers["execution_quality"].severity = "CRITICAL"
                logger.critical(f"[CIRCUIT_BREAKER] 🚨 EXECUTION QUALITY BREAKER TRIPPED: {self.breakers['execution_quality'].reason}")
                return True
        else:
            self.consecutive_slippage_breaches = 0
            self.breakers["execution_quality"].is_tripped = False
        return self.breakers["execution_quality"].is_tripped

    def record_api_latency(self, latency_seconds: float) -> bool:
        """Checks if median API response time > 2.0s.

        Non-finite or negative latencies (timeouts, clock errors) are recorded
        as infinitely slow samples so they push the median up instead of
        making it NaN.
        """
        latency = finite_float(latency_seconds)
        self.recent_latencies.append(latency if latency is not None and latency >= 0 else math.inf)
        if len(self.recent_latencies) > LATENCY_WINDOW:
            self.recent_latencies.pop(0)

        median_lat = float(np.median(self.recent_latencies)) if self.recent_latencies else 0.0
        if median_lat > 2.0:
            self._trip("api_latency", f"Median API latency {median_lat:.2f}s > 2.0s (reducing order frequency)")
            return True
        self.breakers["api_latency"].is_tripped = False
        return False

    def get_status_summary(self) -> dict[str, Any]:
        """Returns snapshot of all circuit breakers."""
        any_tripped = any(b.is_tripped for b in self.breakers.values())
        return {
            "any_circuit_breaker_active": any_tripped,
            "breakers": {k: asdict(v) for k, v in self.breakers.items()}
        }


# Backwards-compatible re-export (imported last: risk.live_enforcer does not
# import this module, so there is no cycle).
from risk.live_enforcer import LiveEnforcerStatus, LiveRiskEnforcer  # noqa: E402

__all__ = ["CircuitBreakerEngine", "CircuitBreakerStatus", "LiveEnforcerStatus", "LiveRiskEnforcer"]
