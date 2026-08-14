import json
import os
import time

class AlertManager:
    """Manages system alerts with deduplication and resolution."""
    def __init__(self, filename="alerts.json"):
        self.filename = filename
        self.active_alerts = {}
        self.historical_alerts = []
        
        self._load()
        
    def _load(self):
        if not os.path.exists(self.filename):
            return
        try:
            with open(self.filename, 'r') as f:
                data = json.load(f)
                self.active_alerts = data.get("active", {})
                self.historical_alerts = data.get("historical", [])
        except:
            pass
            
    def _save(self):
        try:
            tmp = self.filename + ".tmp"
            with open(tmp, 'w') as f:
                json.dump({
                    "active": self.active_alerts,
                    "historical": self.historical_alerts
                }, f, indent=4)
            os.replace(tmp, self.filename)
        except:
            pass
            
    def raise_alert(self, alert_type: str, severity: str, message: str, entity_id: str):
        """Raises an alert. Deduplicates if same type and entity already active."""
        key = f"{alert_type}_{entity_id}"
        now = time.time()
        
        if key in self.active_alerts:
            self.active_alerts[key]["count"] += 1
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
