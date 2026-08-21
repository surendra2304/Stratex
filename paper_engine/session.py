import json
import os
import time
import uuid


class SessionState:
    """Manages the lifecycle state of a trading or paper session."""
    
    def __init__(self, filename="session_state.json"):
        self.filename = filename
        self.session_id = None
        self.status = "STOPPED"
        self.start_time = 0.0
        self.end_time = 0.0
        self.config_snapshot = {}
        
        self._load()
        
    def _load(self):
        if not os.path.exists(self.filename):
            return
        try:
            with open(self.filename, 'r') as f:
                data = json.load(f)
                self.session_id = data.get("session_id")
                self.status = data.get("status", "STOPPED")
                self.start_time = data.get("start_time", 0.0)
                self.end_time = data.get("end_time", 0.0)
                self.config_snapshot = data.get("config_snapshot", {})
        except:
            pass
            
    def _save(self):
        try:
            tmp = self.filename + ".tmp"
            with open(tmp, 'w') as f:
                json.dump({
                    "session_id": self.session_id,
                    "status": self.status,
                    "start_time": self.start_time,
                    "end_time": self.end_time,
                    "config_snapshot": self.config_snapshot
                }, f, indent=4)
            os.replace(tmp, self.filename)
        except Exception as e:
            from paper_engine.exceptions import PersistenceError
            raise PersistenceError(f"Failed to save session state: {e}")
            
    def start_session(self, config_snapshot: dict):
        if self.status == "RUNNING":
            # Previous session crashed
            self.status = "PREVIOUS_SESSION_CRASHED"
            self._save()
            
        self.session_id = str(uuid.uuid4())
        self.status = "RUNNING"
        self.start_time = time.time()
        self.end_time = 0.0
        self.config_snapshot = config_snapshot
        self._save()
        return self.session_id
        
    def stop_session(self):
        self.status = "STOPPED"
        self.end_time = time.time()
        self._save()
        
    def crash_session(self):
        self.status = "CRASHED"
        self.end_time = time.time()
        self._save()
