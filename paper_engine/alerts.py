"""paper_engine/alerts.py — deduplicating alert registry persisted to JSON.

Persistence contract: writes are atomic and cross-process locked
(:mod:`atomic_io`); an unreadable or structurally invalid file is quarantined
(``<file>.corrupt-<UTC>``) and reported instead of being silently dropped, and
the resolved-alert history is bounded.
"""
import logging
import time
from typing import Any

from atomic_io import atomic_write_json, load_json_state, locked_path, quarantine_file

logger = logging.getLogger("paper_engine.alerts")

# Resolved alerts kept on disk; the oldest are dropped beyond this.
HISTORY_LIMIT = 5000


class AlertManager:
    """Manages system alerts with deduplication and resolution."""

    def __init__(self, filename: str = "alerts.json"):
        self.filename = filename
        self.active_alerts: dict[str, dict[str, Any]] = {}
        self.historical_alerts: list[dict[str, Any]] = []
        self.load_status = "missing"
        self.quarantined_to: str | None = None
        self.save_failures = 0
        self._load()

    def _load(self) -> None:
        with locked_path(self.filename):
            result = load_json_state(self.filename, expected_type=dict)
            self.load_status = result.status
            if result.status == "missing":
                return
            if result.status == "corrupt":
                self.quarantined_to = result.quarantined_to
                logger.critical("Alert state %s is corrupt (%s); quarantined to %s", self.filename, result.error, result.quarantined_to)
                return
            active = result.data.get("active", {})
            historical = result.data.get("historical", [])
            if not isinstance(active, dict) or not all(isinstance(v, dict) for v in active.values()) \
                    or not isinstance(historical, list):
                self.quarantined_to = quarantine_file(self.filename, "alert state has the wrong shape")
                self.load_status = "corrupt"
                logger.critical("Alert state %s has the wrong shape; quarantined to %s", self.filename, self.quarantined_to)
                return
            self.active_alerts = active
            self.historical_alerts = [item for item in historical if isinstance(item, dict)][-HISTORY_LIMIT:]

    def _save(self) -> bool:
        if len(self.historical_alerts) > HISTORY_LIMIT:
            del self.historical_alerts[: len(self.historical_alerts) - HISTORY_LIMIT]
        try:
            with locked_path(self.filename):
                atomic_write_json(
                    self.filename,
                    {"active": self.active_alerts, "historical": self.historical_alerts},
                    indent=4,
                    allow_nan=False,
                    default=str,
                )
            return True
        except (OSError, TypeError, ValueError) as exc:
            self.save_failures += 1
            logger.error("Failed to persist alerts to %s: %s", self.filename, exc)
            return False

    def raise_alert(self, alert_type: str, severity: str, message: str, entity_id: str):
        """Raises an alert. Deduplicates if same type and entity already active."""
        key = f"{alert_type}_{entity_id}"
        now = time.time()

        if key in self.active_alerts:
            self.active_alerts[key]["count"] = int(self.active_alerts[key].get("count", 0) or 0) + 1
            self.active_alerts[key]["last_seen"] = now
        else:
            self.active_alerts[key] = {
                "type": alert_type,
                "severity": severity,
                "message": message,
                "entity_id": entity_id,
                "first_seen": now,
                "last_seen": now,
                "count": 1
            }
        self._save()

    def resolve_alert(self, alert_type: str, entity_id: str):
        """Marks an alert as resolved and moves it to historical."""
        key = f"{alert_type}_{entity_id}"
        if key in self.active_alerts:
            alert = self.active_alerts.pop(key)
            alert["resolved_at"] = time.time()
            self.historical_alerts.append(alert)
            self._save()
