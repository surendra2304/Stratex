"""
exchanges/health_monitor.py — Multi-Exchange Real-Time Health & Incident Monitor.

Tracks:
1. REST API Latency, Error Rate, and Timeout frequency per exchange.
2. Order fill rates and WebSocket streaming heartbeats.
3. Per-Exchange Circuit Breaker: 5 consecutive failures = stop trading on that venue for 10 minutes.
4. Automatic Degradation: Proportional flow reduction based on dynamic Health Score (0 - 100).
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
    order_fill_count: int = 0
    order_submission_count: int = 0
    order_fill_rate: float = 1.0
    websocket_connected: bool = True
    circuit_breaker_active: bool = False
    circuit_breaker_tripped_at: Optional[float] = None
    circuit_breaker_cooldown_seconds: float = 600.0  # 10 minutes
    last_ping_time: float = field(default_factory=time.time)


class MultiExchangeHealthMonitor:
    """
    Monitors reliability across connected exchanges and manages venue-specific circuit breakers.
    """

    def __init__(self, exchange_ids: List[str], max_failures_threshold: int = 3):
        self.max_failures_threshold = max_failures_threshold
        self.metrics: Dict[str, ExchangeHealthMetrics] = {
            ex_id: ExchangeHealthMetrics(exchange_id=ex_id) for ex_id in exchange_ids
        }

    def record_heartbeat(
        self,
        exchange_id: str,
        latency_ms: float,
        is_success: bool = True,
        websocket_ok: bool = True
    ) -> None:
        """Updates health statistics on network ping or order response."""
        if exchange_id not in self.metrics:
            self.metrics[exchange_id] = ExchangeHealthMetrics(exchange_id=exchange_id)

        m = self.metrics[exchange_id]
        m.last_ping_time = time.time()
        m.avg_latency_ms = round((m.avg_latency_ms * 0.8) + (latency_ms * 0.2), 1)
        m.websocket_connected = websocket_ok

        # Auto-cooldown expiration check (10 minutes)
        if m.circuit_breaker_active and m.circuit_breaker_tripped_at:
            elapsed = time.time() - m.circuit_breaker_tripped_at
            if elapsed >= m.circuit_breaker_cooldown_seconds:
                self.reset_circuit_breaker(exchange_id)

        if is_success:
            m.consecutive_failures = 0
            if not m.circuit_breaker_active:
                if m.avg_latency_ms < 200.0 and m.websocket_connected:
                    m.status = "HEALTHY"
                    m.health_score = 100
                elif m.avg_latency_ms < 500.0:
                    m.status = "DEGRADED"
                    m.health_score = 75
                else:
                    m.status = "DEGRADED"
                    m.health_score = 50
        else:
            m.consecutive_failures += 1
            m.error_count_last_hour += 1
            m.health_score = max(0, m.health_score - 20)

            # Circuit breaker trips on max_failures_threshold (default 3-5 failures) for 10 minutes
            if m.consecutive_failures >= self.max_failures_threshold:
                m.circuit_breaker_active = True
                m.circuit_breaker_tripped_at = time.time()
                m.status = "CIRCUIT_BREAKER_TRIPPED"
                m.health_score = 0
                logger.critical(f"[EXCHANGE_HEALTH] 🚨 TRIP CIRCUIT BREAKER FOR {exchange_id.upper()} ({m.consecutive_failures} failures). Cooldown: 10 min.")

    def record_order_result(self, exchange_id: str, is_filled: bool) -> None:
        """Tracks order submission and fill counts to compute fill rate."""
        if exchange_id not in self.metrics:
            self.metrics[exchange_id] = ExchangeHealthMetrics(exchange_id=exchange_id)
        m = self.metrics[exchange_id]
        m.order_submission_count += 1
        if is_filled:
            m.order_fill_count += 1
        m.order_fill_rate = round(m.order_fill_count / max(1, m.order_submission_count), 4)

    def reset_circuit_breaker(self, exchange_id: str) -> None:
        """Resets circuit breaker after probe passes or cooldown expires."""
        if exchange_id in self.metrics:
            m = self.metrics[exchange_id]
            m.circuit_breaker_active = False
            m.circuit_breaker_tripped_at = None
            m.consecutive_failures = 0
            m.status = "HEALTHY"
            m.health_score = 100
            logger.info(f"[EXCHANGE_HEALTH] Reset circuit breaker for {exchange_id.upper()}. Venue operational.")

    def is_exchange_available(self, exchange_id: str) -> bool:
        """Returns True if the exchange is eligible for routing."""
        if exchange_id not in self.metrics:
            return True
        m = self.metrics[exchange_id]
        if m.circuit_breaker_active:
            # Check if 10m cooldown expired
            if m.circuit_breaker_tripped_at and (time.time() - m.circuit_breaker_tripped_at) >= m.circuit_breaker_cooldown_seconds:
                self.reset_circuit_breaker(exchange_id)
                return True
            return False
        return True

    def get_flow_allocation_multiplier(self, exchange_id: str) -> float:
        """Returns proportional flow multiplier (0.0 - 1.0) based on health score."""
        if not self.is_exchange_available(exchange_id):
            return 0.0
        m = self.metrics.get(exchange_id)
        if not m:
            return 1.0
        return round(m.health_score / 100.0, 2)

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
                    "consecutive_failures": m.consecutive_failures,
                    "order_fill_rate": m.order_fill_rate,
                    "websocket_connected": m.websocket_connected,
                    "circuit_breaker_active": m.circuit_breaker_active,
                    "cooldown_remaining_seconds": max(0, int(m.circuit_breaker_cooldown_seconds - (time.time() - m.circuit_breaker_tripped_at))) if m.circuit_breaker_active and m.circuit_breaker_tripped_at else 0
                }
                for ex_id, m in self.metrics.items()
            }
        }

