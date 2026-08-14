# ==============================================================================
# LOGGER.PY - Trade Logger: saves every trade to a CSV for performance analysis
# ==============================================================================
import csv
import os
from datetime import datetime

LOG_FILE = "trade_log.csv"
HEADERS = ["timestamp", "strategy", "symbol", "side", "quantity", "price", "sl", "tp", "order_id", "status"]

def init_log():
    """Creates the CSV log file with headers if it does not exist."""
    if not os.path.exists(LOG_FILE):
        with open(LOG_FILE, "w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(HEADERS)
        print(f"[LOGGER] Trade log created: {LOG_FILE}")

def log_trade(strategy, symbol, side, quantity, price, sl, tp, order_id, status):
    """Appends a trade record to the CSV log file."""
    with open(LOG_FILE, "a", newline="") as f:
        writer = csv.writer(f)
        writer.writerow([
            datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            strategy, symbol, side, quantity, price, sl, tp, order_id, status
        ])
    print(f"[LOGGER] Trade logged: {side} {quantity} {symbol} @ {price}")
