import json
import time
import os
from typing import Optional

class HeartbeatState:
    """
    Maintains heartbeat state for Bot Health and Data Health monitoring.
    Writes explicitly to a file so dashboard can read it without locks.
    """
    def __init__(self, filename="heartbeat.json", timeout_seconds=300):
        self.filename = filename
        self.timeout_seconds = timeout_seconds
        
        self.last_process_heartbeat: float = 0.0
        self.last_market_data: float = 0.0
        
        self._load()
        
    def _load(self):
        if not os.path.exists(self.filename):
            return
        try:
            with open(self.filename, 'r') as f:
                data = json.load(f)
                self.last_process_heartbeat = data.get("last_process_heartbeat", 0.0)
                self.last_market_data = data.get("last_market_data", 0.0)
        except Exception:
            pass
            
    def _save(self):
        # Atomic save
        try:
            tmp = self.filename + ".tmp"
            with open(tmp, 'w') as f:
                json.dump({
                    "last_process_heartbeat": self.last_process_heartbeat,
                    "last_market_data": self.last_market_data
                }, f)
            os.replace(tmp, self.filename)
        except Exception:
            pass
            
    def ping_process(self):
        self.last_process_heartbeat = time.time()
        self._save()
        
    def ping_data(self):
        self.last_market_data = time.time()
        self._save()
        
    def get_status(self):
        now = time.time()
        
        if self.last_process_heartbeat == 0.0:
            bot_health = "UNKNOWN"
        elif now - self.last_process_heartbeat > self.timeout_seconds:
            bot_health = "OFFLINE"
        else:
            bot_health = "OK"
            
        if self.last_market_data == 0.0:
            data_health = "UNKNOWN"
        elif now - self.last_market_data > self.timeout_seconds:
            data_health = "STALE"
        else:
            data_health = "OK"
            
        return bot_health, data_health
