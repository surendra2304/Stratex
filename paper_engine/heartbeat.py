"""
paper_engine/heartbeat.py

Heartbeat state tracking for the forward validation experiment.
Every component reports OK / DEGRADED / CRITICAL / OFFLINE.
A CRITICAL or OFFLINE state on market_data or persistence blocks new trades.
"""
import json
import time
from enum import Enum

from logger import get_logger

logger = get_logger("heartbeat")


class ComponentStatus(str, Enum):
    OK = "OK"
    HEALTHY = "HEALTHY"    # alias used by Stage 11/12 tests
    DEGRADED = "DEGRADED"
    CRITICAL = "CRITICAL"
    OFFLINE = "OFFLINE"


class HeartbeatState:
    """Tracks health status of all key forward-runner components."""

    COMPONENTS = [
        "market_data", "strategy", "execution", "portfolio",
        "persistence", "reconciliation",
    ]

    def __init__(
        self,
        heartbeat_file: str = "forward_heartbeat.jsonl",
        filename: str | None = None,          # alias — used by older tests
        timeout_seconds: int | None = None,   # accepted but unused in this impl
    ):
        # Support old callers that pass filename= keyword
        if filename is not None:
            heartbeat_file = filename
        self.heartbeat_file = heartbeat_file
        self.timeout_seconds = timeout_seconds
        self._state = {c: ComponentStatus.OK for c in self.COMPONENTS}
        self._last_update = {c: time.time() for c in self.COMPONENTS}

    def set(self, component: str, status: ComponentStatus):
        if component not in self.COMPONENTS:
            raise ValueError(f"Unknown component: {component}")
        prev = self._state.get(component)
        self._state[component] = status
        self._last_update[component] = time.time()
        if prev != status:
            logger.info(f"Health change: {component} {prev} → {status}")
        self._write_heartbeat()

    def get(self, component: str) -> ComponentStatus:
        return self._state.get(component, ComponentStatus.OFFLINE)

    def is_safe_to_trade(self) -> bool:
        """Returns False if any blocking component is CRITICAL or OFFLINE."""
        blocking = {"market_data", "portfolio", "persistence"}
        for comp in blocking:
            s = self._state.get(comp, ComponentStatus.OFFLINE)
            if s in (ComponentStatus.CRITICAL, ComponentStatus.OFFLINE):
                return False
        return True

    def get_summary(self) -> dict:
        return {c: self._state[c].value for c in self.COMPONENTS}

    @property
    def components(self) -> dict:
        """
        Backward-compatible dict for old tests that access hb.components["Market Data"]["status"].
        Maps friendly names → {"status": value}.
        """
        name_map = {
            "Market Data": "market_data",
            "market_data": "market_data",
            "Strategy": "strategy",
            "strategy": "strategy",
            "Execution": "execution",
            "execution": "execution",
            "Portfolio": "portfolio",
            "portfolio": "portfolio",
            "Persistence": "persistence",
            "persistence": "persistence",
            "Reconciliation": "reconciliation",
            "reconciliation": "reconciliation",
            "Bot": "strategy",
        }
        result = {}
        for friendly, key in name_map.items():
            result[friendly] = {"status": self._state.get(key, ComponentStatus.OK).value}
        return result

    def _write_heartbeat(self):
        record = {
            "timestamp": time.time(),
            "components": self.get_summary(),
        }
        try:
            with open(self.heartbeat_file, "a", encoding="utf-8") as f:
                f.write(json.dumps(record) + "\n")
        except Exception as e:
            logger.error(f"Heartbeat write failed: {e}")

    # ── Backward-compatible API (used by Stage 11/12 tests) ──────────────────

    def ping(self, component_name: str):
        """Record a heartbeat ping for a named component (old API)."""
        # Normalise name to known component keys
        key = component_name.lower().replace(" ", "_")
        if key not in self.COMPONENTS:
            # Accept unknown names gracefully — store in last_update only
            key = "strategy"
        self._last_update[key] = time.time()

    def get_overall_health(self) -> ComponentStatus:
        """
        Returns HEALTHY if all recent pings are within timeout, else OFFLINE.
        Used by Stage 11/12 acceptance tests.
        """
        if self.timeout_seconds is None:
            return ComponentStatus.HEALTHY
        now = time.time()
        for last in self._last_update.values():
            if (now - last) > self.timeout_seconds:
                return ComponentStatus.OFFLINE
        return ComponentStatus.HEALTHY

