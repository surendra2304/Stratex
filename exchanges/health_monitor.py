"""
exchanges/health_monitor.py — Multi-Exchange Real-Time Health & Incident Monitor.

Tracks:
1. REST API Latency, Error Rate, and Timeout frequency per exchange.
2. WebSocket Streaming Heartbeats.
3. Per-Exchange Circuit Breaker: Automatically de-routes traffic from faulty venues after 3 consecutive failures.
4. Consolidated Health Score (0 - 100).
"""

import time
import datetime
from typing import Dict, List, Optional, Tuple, Any
from dataclasses import dataclass, field
from logger import get_logger

logger = get_logger("exchange_health")


@dataclass
class ExchangeHealthMetrics:
    exchange_id: str
    status: str = "HEALTHY"  # "HEALTHY", "DEGRADED", "CIRCUIT_BREAKER_TRIPPED"
    health_score: int = 100
    avg_latency_ms: float = 45.0
    error_count_last_hour: int = 0
    consecutive_failures: int = 0
    circuit_breaker_active: bool = False
    last_ping_time: float = field(default_factory=time.time)


class MultiExchangeHealthMonitor:
    """
    Monitors reliability across connected exchanges and manages venue-specific circuit breakers.
    """

    def __init__(self, exchange_ids: List[str]):
        self.metrics: Dict[str, ExchangeHealthMetrics] = {
            ex_id: ExchangeHealthMetrics(exchange_id=ex_id) for ex_id in exchange_ids
        }

    def record_heartbeat(self, exchange_id: str, latency_ms: float, is_success: bool = True) -> None:
        """Updates health statistics on network ping or order response."""
        if exchange_id not in self.metrics:
            self.metrics[exchange_id] = ExchangeHealthMetrics(exchange_id=exchange_id)

        m = self.metrics[exchange_id]
        m.last_ping_time = time.time()
        m.avg_latency_ms = round((m.avg_latency_ms * 0.8) + (latency_ms * 0.2), 1)

        if is_success:
            m.consecutive_failures = 0
            if not m.circuit_breaker_active:
                m.status = "HEALTHY" if m.avg_latency_ms < 300.0 else "DEGRADED"
                m.health_score = 100 if m.status == "HEALTHY" else 75
        else:
            m.consecutive_failures += 1
            m.error_count_last_hour += 1
            m.health_score = max(0, m.health_score - 25)

            if m.consecutive_failures >= 3:
                m.circuit_breaker_active = True
                m.status = "CIRCUIT_BREAKER_TRIPPED"
                m.health_score = 0
                logger.critical(f"[EXCHANGE_HEALTH] 🚨 TRIP CIRCUIT BREAKER FOR {exchange_id.upper()} ({m.consecutive_failures} failures).")

    def reset_circuit_breaker(self, exchange_id: str) -> None:
        """Manually or automatically resets circuit breaker after probe passes."""
        if exchange_id in self.metrics:
            m = self.metrics[exchange_id]
            m.circuit_breaker_active = False
            m.consecutive_failures = 0
            m.status = "HEALTHY"
            m.health_score = 100
            logger.info(f"[EXCHANGE_HEALTH] Reset circuit breaker for {exchange_id.upper()}.")

    def get_all_health_statuses(self) -> Dict[str, Any]:
        """Gathers multi-exchange health telemetry snapshot."""
        return {
            "timestamp": datetime.datetime.utcnow().isoformat() + "Z",
            "exchanges": {
                ex_id: {
                    "status": m.status,
                    "health_score": m.health_score,
                    "avg_latency_ms": m.avg_latency_ms,
                    "error_count_last_hour": m.error_count_last_hour,
                    "circuit_breaker_active": m.circuit_breaker_active
                }
                for ex_id, m in self.metrics.items()
            }
        }
