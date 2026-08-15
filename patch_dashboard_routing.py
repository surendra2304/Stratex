import os

dashboard_file = 'd:/MT5/python_bot/dashboard.py'
with open(dashboard_file, 'r', encoding='utf-8') as f:
    content = f.read()

# Route /api/status to testnet_portfolio if TESTNET
old_status = '''    if os.path.exists("paper_portfolio.json"):
        try:
            import json
            with open("paper_portfolio.json", "r") as f:'''

new_status = '''    from config import TRADING_MODE
    portfolio_file = "testnet_portfolio.json" if TRADING_MODE == "TESTNET" else "paper_portfolio.json"
    if os.path.exists(portfolio_file):
        try:
            import json
            with open(portfolio_file, "r") as f:'''
content = content.replace(old_status, new_status)

# Also fix the temp_port fallback for drawdown
old_mdd = '''            try:
                from paper_engine.portfolio import PaperPortfolio
                temp_port = PaperPortfolio("paper_portfolio.json")
                mdd = temp_port.get_max_drawdown() * 100
            except Exception as e:'''

new_mdd = '''            try:
                if TRADING_MODE == "PAPER":
                    from paper_engine.portfolio import PaperPortfolio
                    temp_port = PaperPortfolio("paper_portfolio.json")
                    mdd = temp_port.get_max_drawdown() * 100
                else:
                    mdd = port.get("max_drawdown", 0.0) * 100
            except Exception as e:'''
content = content.replace(old_mdd, new_mdd)

# Route /api/trades to testnet ledger (handle both instances)
old_trades = '''        # 1. Parse closed trades from ledger
        if os.path.exists("paper_trade_ledger.jsonl"):
            active_exp_id = "UNKNOWN"'''

new_trades = '''        # 1. Parse closed trades from ledger
        ledger_file = "testnet_trade_ledger.jsonl" if TRADING_MODE == "TESTNET" else "paper_trade_ledger.jsonl"
        if os.path.exists(ledger_file):
            active_exp_id = "UNKNOWN"'''
content = content.replace(old_trades, new_trades)

old_trades_open = '''            with open("paper_trade_ledger.jsonl", "r") as f:'''
new_trades_open = '''            with open(ledger_file, "r") as f:'''
content = content.replace(old_trades_open, new_trades_open)

old_trades_legacy = '''                        # EXCLUDE LEGACY DATA
                        if trade_exp_id != active_exp_id:
                            continue'''
new_trades_legacy = '''                        # EXCLUDE LEGACY DATA (Paper only)
                        if TRADING_MODE == "PAPER" and trade_exp_id != active_exp_id:
                            continue'''
content = content.replace(old_trades_legacy, new_trades_legacy)

old_trades_port = '''        # 2. Add open positions from portfolio
        if os.path.exists("paper_portfolio.json"):
            try:
                with open("paper_portfolio.json", "r") as f:'''
new_trades_port = '''        # 2. Add open positions from portfolio
        port_file = "testnet_portfolio.json" if TRADING_MODE == "TESTNET" else "paper_portfolio.json"
        if os.path.exists(port_file):
            try:
                with open(port_file, "r") as f:'''
content = content.replace(old_trades_port, new_trades_port)

# Allow TRADING_MODE == TESTNET in /api/trades logic:
old_mode = '''    if TRADING_MODE == "PAPER":'''
new_mode = '''    if TRADING_MODE in ["PAPER", "TESTNET"]:'''
content = content.replace(old_mode, new_mode)

with open(dashboard_file, 'w', encoding='utf-8') as f:
    f.write(content)
print('Dashboard patched for TESTNET routing')
