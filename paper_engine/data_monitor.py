import time
from typing import Dict, Optional

class DataMonitor:
    """Monitors market data streams for anomalies, gaps, and staleness."""
    
    def __init__(self, heartbeat):
        self.heartbeat = heartbeat
        self.symbols: Dict[str, dict] = {}
        
    def _ensure_symbol(self, symbol: str):
        if symbol not in self.symbols:
            self.symbols[symbol] = {
                "last_timestamp": 0.0,
                "last_price": 0.0,
                "gaps": 0,
                "duplicates": 0,
                "out_of_order": 0
            }
            
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
        
        # Ping health component
        from paper_engine.heartbeat import ComponentStatus
        self.heartbeat.ping("Market Data", ComponentStatus.OK)
        
    def check_staleness(self, max_stale_sec=300):
        now = time.time()
        for sym, state in self.symbols.items():
            if now - state["last_timestamp"] > max_stale_sec:
                from paper_engine.heartbeat import ComponentStatus
                self.heartbeat.ping("Market Data", ComponentStatus.STALE)
                return False
        return True
