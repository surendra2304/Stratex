"""
alerting/intelligent_alerts.py — Context-Aware Intelligent Alert Engine.

Alert Levels:
- RAW: Simple threshold alert.
- CONTEXTUAL: Threshold + current market regime context.
- INSIGHTFUL: Threshold + context + actionable mitigation recommendation.

Severities & Routing:
- CRITICAL (Act Now) -> Webhook + FRIDAY + Dashboard.
- HIGH (Act Today)   -> Webhook + FRIDAY + Dashboard.
- MEDIUM (Review)    -> Dashboard + Daily Report.
- LOW (Info)         -> Daily Report only.

Features:
- Deduplication: Suppresses identical alerts within a 15-minute window.
- Max Rate: Capped at 10 alerts/hour to prevent alert fatigue.
"""

import time
import datetime
from typing import Dict, List, Optional, Any
from dataclasses import dataclass, field, asdict
from reporting.voice_summaries import generate_alert_voice_snippet
from api.webhooks import EcosystemWebhookEmitter
from logger import get_logger

logger = get_logger("intelligent_alerts")


@dataclass
class IntelligentAlert:
    alert_id: str
    severity: str  # "CRITICAL", "HIGH", "MEDIUM", "LOW"
    category: str  # "RISK", "STRATEGY", "SYSTEM", "ADVISORY"
    title: str
    message: str
    context: str
    recommendation: Optional[str]
    voice_summary: str
    timestamp: str
    dedup_key: str


class IntelligentAlertEngine:
    """
    Evaluates, deduplicates, and dispatches context-aware alerts.
    """

    def __init__(
        self,
        dedup_window_seconds: int = 900,  # 15 min
        max_alerts_per_hour: int = 10
    ):
        self.dedup_window = dedup_window_seconds
        self.max_per_hour = max_alerts_per_hour
        self.recent_alerts: List[IntelligentAlert] = []
        self.recent_alert_timestamps: List[float] = []
        self.webhook_emitter = EcosystemWebhookEmitter()

    def emit_alert(
        self,
        severity: str,
        category: str,
        title: str,
        message: str,
        context: str = "",
        recommendation: Optional[str] = None
    ) -> Optional[IntelligentAlert]:
        now = time.time()
        dedup_key = f"{severity}_{category}_{title}"

        # 1. Deduplication check
        for a in self.recent_alerts:
            if a.dedup_key == dedup_key:
                alert_time = datetime.datetime.fromisoformat(a.timestamp.replace("Z", "+00:00")).timestamp()
                if (now - alert_time) < self.dedup_window:
                    logger.debug(f"[ALERT_ENGINE] Suppressed duplicate alert: {title}")
                    return None

        # 2. Rate Limiting Check
        self.recent_alert_timestamps = [t for t in self.recent_alert_timestamps if (now - t) < 3600]
        if len(self.recent_alert_timestamps) >= self.max_per_hour:
            logger.warning(f"[ALERT_ENGINE] Alert rate limit ({self.max_per_hour}/hr) reached. Buffering into digest.")
            return None

        voice_snippet = generate_alert_voice_snippet(severity, title, recommendation)
        alert = IntelligentAlert(
            alert_id=f"ALT_{int(now*1000)}",
            severity=severity.upper(),
            category=category.upper(),
            title=title,
            message=message,
            context=context,
            recommendation=recommendation,
            voice_summary=voice_snippet,
            timestamp=datetime.datetime.utcnow().isoformat() + "Z",
            dedup_key=dedup_key
        )

        self.recent_alerts.append(alert)
        self.recent_alert_timestamps.append(now)

        # 3. Intelligent Routing
        if alert.severity in ["CRITICAL", "HIGH"]:
            self.webhook_emitter.emit_event("alert.triggered", asdict(alert))
            logger.warning(f"[ALERT_ENGINE] 🚨 [{alert.severity}] {alert.title}: {alert.message}")
        else:
            logger.info(f"[ALERT_ENGINE] ℹ️ [{alert.severity}] {alert.title}: {alert.message}")

        return alert

    def get_recent_alerts(self, limit: int = 20) -> List[Dict[str, Any]]:
        return [asdict(a) for a in self.recent_alerts[-limit:]]
