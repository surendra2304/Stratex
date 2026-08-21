# ==============================================================================
# LOGGER.PY - Trade Logger: saves every trade to a CSV for performance analysis
# ==============================================================================
import csv
import json
import logging
import os
from datetime import datetime
from logging.handlers import RotatingFileHandler

from config import TRADING_MODE

# --- Trade CSV Logging (Unbounded) ---
LOG_FILE = "trade_log.csv"
HEADERS = ["timestamp", "strategy", "symbol", "side", "quantity", "price", "sl", "tp", "order_id", "status"]

def init_log():
    """Creates the CSV log file with headers if it does not exist."""
    if not os.path.exists(LOG_FILE):
        with open(LOG_FILE, "w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(HEADERS)

def log_trade(strategy, symbol, side, quantity, price, sl, tp, order_id, status):
    """Appends a trade record to the CSV log file."""
    with open(LOG_FILE, "a", newline="") as f:
        writer = csv.writer(f)
        writer.writerow([
            datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            strategy, symbol, side, quantity, price, sl, tp, order_id, status
        ])

# --- Structured System Logging (Bounded) ---
class JSONFormatter(logging.Formatter):
    def format(self, record):
        log_record = {
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "level": record.levelname,
            "component": record.name,
            "message": record.getMessage(),
            "mode": TRADING_MODE
        }
        
        # Add any extra arguments passed via 'extra' dictionary
        if hasattr(record, "strategy"):
            log_record["strategy"] = record.strategy
        if hasattr(record, "symbol"):
            log_record["symbol"] = record.symbol
        if hasattr(record, "event_id"):
            log_record["event_id"] = record.event_id
            
        return json.dumps(log_record)

class SafeRotatingFileHandler(RotatingFileHandler):
    """Subclass of RotatingFileHandler that silently handles Windows file lock race conditions during rollover."""
    def doRollover(self):
        try:
            super().doRollover()
        except (PermissionError, OSError):
            pass

def get_logger(name="system"):
    """
    Returns a configured structured logger.
    Logs to bot.log with safe rotation (Max 10MB, 5 backups).
    """
    logger = logging.getLogger(name)
    
    # Only configure once
    if not logger.handlers:
        logger.setLevel(logging.INFO)
        
        log_file = "test_bot.log" if os.environ.get("PYTEST_CURRENT_TEST") or os.environ.get("TESTING") else "bot.log"
        handler = SafeRotatingFileHandler(
            log_file, 
            maxBytes=10 * 1024 * 1024, # 10 MB
            backupCount=5
        )
        handler.setFormatter(JSONFormatter())
        
        # Also log to console but without JSON for readability
        console = logging.StreamHandler()
        console.setFormatter(logging.Formatter('[%(levelname)s] [%(name)s] %(message)s'))
        
        logger.addHandler(handler)
        logger.addHandler(console)
        
    return logger

