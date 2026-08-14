import time

class StrategyMonitor:
    """Monitors signals and strategy evaluation for health."""
    def __init__(self, heartbeat):
        self.heartbeat = heartbeat
        self.last_signal_time = 0.0
        self.signal_count = 0
        
    def process_signal(self, symbol: str, side: str, confidence: float):
        from paper_engine.exceptions import StrategyError
        
        # Validation
        if side not in ["BUY", "SELL"]:
            self.heartbeat.report_error("Strategy")
            raise StrategyError(f"Invalid signal side: {side}")
            
        if not (0.0 <= confidence <= 1.0):
            self.heartbeat.report_error("Strategy")
            raise StrategyError(f"Signal confidence out of bounds: {confidence}")
            
        # Update metrics
        self.last_signal_time = time.time()
        self.signal_count += 1
        
        from paper_engine.heartbeat import ComponentStatus
        self.heartbeat.ping("Strategy", ComponentStatus.OK)
        
    def report_unhedged(self, symbol: str, leg: str):
        """Records an unhedged state for pairs/funding."""
        from logger import get_logger
        from paper_engine.heartbeat import ComponentStatus
        
        get_logger("strategy_monitor").warning(f"UNHEDGED state detected on {symbol} {leg}")
        self.heartbeat.ping("Strategy", ComponentStatus.DEGRADED)
