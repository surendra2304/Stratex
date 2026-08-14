import json
import time
import os
from enum import Enum

class ComponentStatus(str, Enum):
    OK = "OK"
    DEGRADED = "DEGRADED"
    CRITICAL = "CRITICAL"
    OFFLINE = "OFFLINE"
    UNKNOWN = "UNKNOWN"
    STALE = "STALE"
    ERROR = "ERROR"

class SystemHealth(str, Enum):
    HEALTHY = "HEALTHY"
    DEGRADED = "DEGRADED"
    CRITICAL = "CRITICAL"
    OFFLINE = "OFFLINE"

class HeartbeatState:
    """
    Maintains heartbeat state for all system components.
    Writes explicitly to a file so dashboard can read it without locks.
    """
    def __init__(self, filename="heartbeat.json", timeout_seconds=300):
        self.filename = filename
        self.timeout_seconds = timeout_seconds
        
        self.components = {
            "Bot": {"last_success": 0.0, "last_error": 0.0, "status": ComponentStatus.UNKNOWN},
            "Market Data": {"last_success": 0.0, "last_error": 0.0, "status": ComponentStatus.UNKNOWN},
            "Strategy": {"last_success": 0.0, "last_error": 0.0, "status": ComponentStatus.UNKNOWN},
            "Execution": {"last_success": 0.0, "last_error": 0.0, "status": ComponentStatus.UNKNOWN},
            "Portfolio": {"last_success": 0.0, "last_error": 0.0, "status": ComponentStatus.UNKNOWN},
            "Persistence": {"last_success": 0.0, "last_error": 0.0, "status": ComponentStatus.UNKNOWN},
            "Dashboard": {"last_success": 0.0, "last_error": 0.0, "status": ComponentStatus.UNKNOWN}
        }
        
        self._load()
        
    def _load(self):
        if not os.path.exists(self.filename):
            return
        try:
            with open(self.filename, 'r') as f:
                data = json.load(f)
                if isinstance(data, dict):
                    # Migration: if old format, ignore
                    if "last_process_heartbeat" in data:
                        return
                    for k, v in data.items():
                        if k in self.components:
                            self.components[k] = v
        except Exception:
            pass
            
    def _save(self):
        try:
            tmp = self.filename + ".tmp"
            with open(tmp, 'w') as f:
                json.dump(self.components, f, indent=4)
            os.replace(tmp, self.filename)
        except Exception:
            pass
            
    def ping(self, component_name: str, status: ComponentStatus = ComponentStatus.OK):
        if component_name in self.components:
            now = time.time()
            self.components[component_name]["last_success"] = now
            if status != ComponentStatus.UNKNOWN:
                self.components[component_name]["status"] = status.value
            self._save()

    def report_error(self, component_name: str):
        if component_name in self.components:
            self.components[component_name]["last_error"] = time.time()
            self.components[component_name]["status"] = ComponentStatus.ERROR.value
            self._save()
        
    def get_overall_health(self) -> SystemHealth:
        now = time.time()
        
        # Determine offline status based on bot core
        bot_last = self.components["Bot"]["last_success"]
        if bot_last == 0.0 or (now - bot_last > self.timeout_seconds):
            return SystemHealth.OFFLINE
            
        critical_components = ["Market Data", "Execution", "Portfolio", "Persistence"]
        
        has_critical_error = False
        has_degraded = False
        
        for k, v in self.components.items():
            status = v.get("status")
            if status in [ComponentStatus.ERROR.value, ComponentStatus.CRITICAL.value, ComponentStatus.STALE.value]:
                if k in critical_components:
                    has_critical_error = True
                else:
                    has_degraded = True
            
            # Check staleness
            if v["last_success"] > 0 and (now - v["last_success"] > self.timeout_seconds):
                if k in critical_components:
                    has_critical_error = True
                else:
                    has_degraded = True
                    
        if has_critical_error:
            return SystemHealth.CRITICAL
        if has_degraded:
            return SystemHealth.DEGRADED
            
        return SystemHealth.HEALTHY
