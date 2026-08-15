import os
import tempfile

# Create a global temporary directory for tests
test_dir = tempfile.mkdtemp(prefix="mt5_test_")

# Set all file paths to this temporary directory BEFORE any modules are imported
os.environ["TESTNET_LEDGER_FILE"] = os.path.join(test_dir, "testnet_trade_ledger.jsonl")
os.environ["TESTNET_OPPORTUNITY_LOG"] = os.path.join(test_dir, "testnet_opportunity_log.jsonl")
os.environ["TESTNET_PORTFOLIO_FILE"] = os.path.join(test_dir, "testnet_portfolio.json")
os.environ["TESTNET_EQUITY_HISTORY_FILE"] = os.path.join(test_dir, "testnet_equity_history.jsonl")
os.environ["ACTIVE_TRADES_FILE"] = os.path.join(test_dir, "active_trades.json")
os.environ["TRADING_MODE"] = "TESTNET"
os.environ["TESTNET_ONLY"] = "TRUE"
