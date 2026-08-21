import time


class DataMonitor:
    """
    Monitors market data streams for anomalies, gaps, and staleness.
    Provides explicit health status transitions:
        HEALTHY -> DEGRADED -> CRITICAL -> OFFLINE
    """

    def __init__(self, heartbeat=None, degraded_threshold=10, critical_threshold=60, offline_threshold=300):
        self.heartbeat = heartbeat
        self.symbols: dict[str, dict] = {}
        self.degraded_threshold = degraded_threshold   # seconds
        self.critical_threshold = critical_threshold    # seconds
        self.offline_threshold = offline_threshold      # seconds
        self._last_received: float = 0.0

    def _ensure_symbol(self, symbol: str):
        if symbol not in self.symbols:
            self.symbols[symbol] = {
                "last_timestamp": 0.0,
                "last_price": 0.0,
                "gaps": 0,
                "duplicates": 0,
                "out_of_order": 0
            }

    def record_data_received(self):
        """Call whenever a valid data tick is received."""
        self._last_received = time.time()

    def get_status(self) -> str:
        """
        Returns explicit health state based on staleness:
          HEALTHY, DEGRADED, CRITICAL, OFFLINE
        """
        if self._last_received == 0.0:
            return "OFFLINE"
        age = time.time() - self._last_received
        if age >= self.offline_threshold:
            return "OFFLINE"
        if age >= self.critical_threshold:
            return "CRITICAL"
        if age >= self.degraded_threshold:
            return "DEGRADED"
        return "HEALTHY"

    def process_tick(self, symbol: str, price: float, timestamp: float, expected_interval_sec: float = 60.0):
        self._ensure_symbol(symbol)
        state = self.symbols[symbol]

        # Anomaly detection
        if state["last_timestamp"] > 0:
            if timestamp < state["last_timestamp"]:
                state["out_of_order"] += 1
                from logger import get_logger
                get_logger("data_monitor").warning(f"Out of order tick for {symbol}")
            elif timestamp == state["last_timestamp"]:
                state["duplicates"] += 1
            else:
                gap = timestamp - state["last_timestamp"]
                # 50% tolerance on interval
                if gap > (expected_interval_sec * 1.5):
                    state["gaps"] += 1
                    from logger import get_logger
                    get_logger("data_monitor").warning(f"Data gap detected for {symbol}: {gap}s")

        state["last_timestamp"] = timestamp
        state["last_price"] = price
        self.record_data_received()

        # Ping health component if heartbeat available
        if self.heartbeat is not None:
            try:
                from paper_engine.heartbeat import ComponentStatus
                self.heartbeat.ping("Market Data", ComponentStatus.OK)
            except Exception:
                pass

    def check_staleness(self, max_stale_sec=300):
        now = time.time()
        for state in self.symbols.values():
            if now - state["last_timestamp"] > max_stale_sec:
                if self.heartbeat is not None:
                    try:
                        from paper_engine.heartbeat import ComponentStatus
                        self.heartbeat.ping("Market Data", ComponentStatus.STALE)
                    except Exception:
                        pass
                return False
        return True
