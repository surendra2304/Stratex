import os
import sys
import tempfile

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

# Create a global temporary directory for tests
test_dir = tempfile.mkdtemp(prefix="mt5_test_")

# Set all file paths to this temporary directory BEFORE any modules are imported
os.environ["TESTNET_LEDGER_FILE"] = os.path.join(test_dir, "testnet_trade_ledger.jsonl")
os.environ["TESTNET_TRADE_EVENTS_FILE"] = os.path.join(test_dir, "testnet_trade_events.jsonl")
os.environ["TESTNET_EXECUTION_EVENTS_FILE"] = os.path.join(test_dir, "testnet_execution_events.jsonl")
os.environ["TESTNET_SIGNALS_LOG_FILE"] = os.path.join(test_dir, "testnet_signals_log.jsonl")
os.environ["TESTNET_BALANCE_EVENTS_FILE"] = os.path.join(test_dir, "testnet_balance_events.jsonl")
os.environ["TESTNET_OPPORTUNITY_LOG"] = os.path.join(test_dir, "testnet_opportunity_log.jsonl")
os.environ["TESTNET_PORTFOLIO_FILE"] = os.path.join(test_dir, "testnet_portfolio.json")
os.environ["TESTNET_EQUITY_HISTORY_FILE"] = os.path.join(test_dir, "testnet_equity_history.jsonl")
os.environ["ACTIVE_TRADES_FILE"] = os.path.join(test_dir, "active_trades.json")
os.environ["FORWARD_RECONCILIATION_FILE"] = os.path.join(test_dir, "forward_reconciliation.jsonl")
os.environ["TRADING_MODE"] = "TESTNET"
os.environ["TESTNET_ONLY"] = "TRUE"

# Clear any secret keys for unauthenticated local test client assertions
for k in ["BOT_API_KEY", "API_KEY_CONTROL", "API_KEY_READONLY", "API_KEY_FRIDAY"]:
    os.environ[k] = ""
