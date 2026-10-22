"""paper_engine/session.py — persisted lifecycle state of a paper session.

Fail-closed contract: an unreadable, invalid or wrong-shaped state file raises
:class:`PersistenceError` (the caller must not guess whether a previous session
crashed); saves are atomic and cross-process locked.
"""
import os
import time
import uuid

from atomic_io import atomic_write_json, load_json_state, locked_path

VALID_STATUSES = {"STOPPED", "RUNNING", "CRASHED", "PREVIOUS_SESSION_CRASHED"}


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
        from paper_engine.exceptions import PersistenceError

        if not os.path.exists(self.filename):
            return
        with locked_path(self.filename):
            result = load_json_state(self.filename, expected_type=dict, quarantine=False)
        if result.status == "missing":
            return
        if result.status != "ok":
            raise PersistenceError(f"Session state is unreadable/corrupt: {self.filename} ({result.error})")
        data = result.data
        status = data.get("status", "STOPPED")
        session_id = data.get("session_id")
        start_time = data.get("start_time", 0.0)
        end_time = data.get("end_time", 0.0)
        snapshot = data.get("config_snapshot", {})
        problems = []
        if status not in VALID_STATUSES:
            problems.append(f"unknown status {status!r}")
        if session_id is not None and not isinstance(session_id, str):
            problems.append("session_id is not a string")
        for name, value in (("start_time", start_time), ("end_time", end_time)):
            if isinstance(value, bool) or not isinstance(value, (int, float)) or value != value or value < 0:
                problems.append(f"{name} is not a non-negative number")
        if not isinstance(snapshot, dict):
            problems.append("config_snapshot is not an object")
        if problems:
            raise PersistenceError(f"Session state is unreadable/corrupt: {self.filename} ({'; '.join(problems)})")
        self.session_id = session_id
        self.status = status
        self.start_time = float(start_time)
        self.end_time = float(end_time)
        self.config_snapshot = snapshot

    def _save(self):
        try:
            with locked_path(self.filename):
                atomic_write_json(self.filename, {
                    "session_id": self.session_id,
                    "status": self.status,
                    "start_time": self.start_time,
                    "end_time": self.end_time,
                    "config_snapshot": self.config_snapshot
                }, indent=4, allow_nan=False, default=str)
        except (OSError, TypeError, ValueError) as e:
            from paper_engine.exceptions import PersistenceError
            raise PersistenceError(f"Failed to save session state: {e}") from e

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
