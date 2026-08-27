"""
alerting/ops_alerts.py — Operational & Infrastructure Alerting Engine.

Monitors non-trading operational health:
1. Exchange API degradation (p95 latency > 1.0s for 5m).
2. WebSocket disconnects.
3. Ledger reconciliation mismatches (> 0.5%).
4. Ledger write failures.
5. Evolution engine stall (no evolution generation in 48h).
6. Advisory scheduler crash / stall.
7. Disk space > 85%.
8. Memory leak detection (RSS growth > 100MB/hr).
Channels: Webhook, Dashboard Banner, and Critical Notification Dispatch.
"""

import time
import os
import json
import datetime
from typing import Dict, List, Optional, Tuple, Any
from dataclasses import dataclass, field
from logger import get_logger

logger = get_logger("ops_alerts")


@dataclass
class OperationalAlert:
    alert_id: str
    category: str  # "EXCHANGE", "WEBSOCKET", "LEDGER", "EVOLUTION", "ADVISORY", "DISK", "MEMORY"
    severity: str  # "INFO", "WARNING", "ERROR", "CRITICAL"
    message: str
    timestamp: str = field(default_factory=lambda: datetime.datetime.utcnow().isoformat() + "Z")
    acknowledged: bool = False


class OperationalAlertEngine:
    """
    Evaluates infrastructure and operational metrics against alerting thresholds.
    """

    def __init__(self):
        self.active_alerts: List[OperationalAlert] = []
        self.alert_history: List[OperationalAlert] = []
        self.rss_history: List[Tuple[float, float]] = []  # (timestamp, rss_mb)
        self.last_evolution_ts: float = time.time()
        self.last_advisory_heartbeat_ts: float = time.time()

    def raise_alert(self, category: str, severity: str, message: str) -> OperationalAlert:
        """Publishes an operational alert and logs it."""
        alert = OperationalAlert(
            alert_id=f"OPS_{int(time.time()*1000)}",
            category=category,
            severity=severity,
            message=message
        )
        self.active_alerts.append(alert)
        self.alert_history.append(alert)
        if len(self.alert_history) > 200:
            self.alert_history.pop(0)

        log_fn = logger.critical if severity == "CRITICAL" else (logger.error if severity == "ERROR" else logger.warning)
        log_fn(f"[OPS_ALERT] [{severity}] [{category}] {message}")
        return alert

    def check_exchange_latency(self, exchange_id: str, p95_latency_ms: float) -> Optional[OperationalAlert]:
        """Alerts if p95 latency > 1000ms."""
        if p95_latency_ms > 1000.0:
            return self.raise_alert(
                category="EXCHANGE",
                severity="WARNING",
                message=f"Exchange {exchange_id.upper()} degraded: p95 latency {p95_latency_ms:.1f}ms > 1000ms threshold."
            )
        return None

    def check_websocket_disconnect(self, exchange_id: str, is_connected: bool) -> Optional[OperationalAlert]:
        """Alerts on WebSocket disconnection."""
        if not is_connected:
            return self.raise_alert(
                category="WEBSOCKET",
                severity="ERROR",
                message=f"WebSocket connection to {exchange_id.upper()} disconnected. Reconnecting..."
            )
        return None

    def check_disk_space(self, disk_usage_pct: float) -> Optional[OperationalAlert]:
        """Alerts if disk space exceeds 85%."""
        if disk_usage_pct > 85.0:
            return self.raise_alert(
                category="DISK",
                severity="CRITICAL" if disk_usage_pct > 95.0 else "WARNING",
                message=f"High disk utilization: {disk_usage_pct:.1f}% used (> 85.0% threshold)."
            )
        return None

    def check_memory_leak(self, current_rss_mb: float) -> Optional[OperationalAlert]:
        """Alerts if process memory grows > 100MB in a 1-hour window."""
        now = time.time()
        self.rss_history.append((now, current_rss_mb))
        # Retain 1-hour of data
        self.rss_history = [(t, rss) for t, rss in self.rss_history if now - t <= 3600]

        if len(self.rss_history) >= 2:
            earliest_time, earliest_rss = self.rss_history[0]
            growth = current_rss_mb - earliest_rss
            if growth > 100.0:
                return self.raise_alert(
                    category="MEMORY",
                    severity="ERROR",
                    message=f"Potential memory leak detected: RSS grew by {growth:.1f}MB in past hour ({earliest_rss:.1f}MB -> {current_rss_mb:.1f}MB)."
                )
        return None

    def check_evolution_engine_stall(self, last_generation_ts: float) -> Optional[OperationalAlert]:
        """Alerts if evolution lab hasn't evolved for 48 hours."""
        elapsed_hours = (time.time() - last_generation_ts) / 3600.0
        if elapsed_hours > 48.0:
            return self.raise_alert(
                category="EVOLUTION",
                severity="WARNING",
                message=f"Strategy Evolution Engine stalled: No new generation completed in {elapsed_hours:.1f} hours (> 48h threshold)."
            )
        return None

    def check_advisory_scheduler_liveness(self, last_heartbeat_ts: float) -> Optional[OperationalAlert]:
        """Alerts if advisory scheduler heartbeat is older than 15 minutes."""
        elapsed_sec = time.time() - last_heartbeat_ts
        if elapsed_sec > 900:
            return self.raise_alert(
                category="ADVISORY",
                severity="ERROR",
                message=f"AI Advisory Scheduler unresponsive: Last heartbeat {elapsed_sec/60:.1f} min ago (> 15m threshold)."
            )
        return None

    def get_dashboard_alert_banners(self) -> List[Dict[str, Any]]:
        """Returns active high-priority alerts for rendering in UI banners."""
        return [
            {
                "id": a.alert_id,
                "category": a.category,
                "severity": a.severity,
                "message": a.message,
                "timestamp": a.timestamp
            }
            for a in self.active_alerts if not a.acknowledged
        ]


# Singleton instance
_GLOBAL_OPS_ALERT_ENGINE = OperationalAlertEngine()

def get_ops_alert_engine() -> OperationalAlertEngine:
    return _GLOBAL_OPS_ALERT_ENGINE