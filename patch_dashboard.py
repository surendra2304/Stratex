import os
import json

dashboard_file = 'd:/MT5/python_bot/dashboard.py'
index_file = 'd:/MT5/python_bot/static/index.html'

with open(dashboard_file, 'r', encoding='utf-8') as f:
    dashboard_content = f.read()

# PATCH 1: In dashboard.py /api/health (get_status)
# Replace the portfolio reading block to handle missing portfolio properly
# and default to 10000 cash/equity.
old_portfolio_block = '''    if os.path.exists("paper_portfolio.json"):
        try:
            import json
            with open("paper_portfolio.json", "r") as f:
                port = json.load(f)
            
            # Fetch recent market prices to compute true equity
            current_price = 0.0
            try:
                from data import get_candles
                df = get_candles("BTCUSDT", "1m", 1)
                if not df.empty:
                    current_price = df['close'].iloc[-1]
            except Exception as e:
                from logger import get_logger
                get_logger("dashboard").warning(f"Failed to fetch live price for equity calc: {e}")
            
            # Compute unrealized
            for pos in port.get("positions", {}).values():
                if pos['status'] == "OPEN" and current_price > 0:
                    if pos['direction'] in ["LONG", "BUY"]:
                        unrealized_pnl += (current_price - pos['entry_price']) * pos['quantity']
                    else:
                        unrealized_pnl += (pos['entry_price'] - current_price) * pos['quantity']
                        
            cash = port.get("cash", 0.0)
            equity = cash + unrealized_pnl
            realized_pnl = port.get("realized_pnl", 0.0)
            fees = port.get("cumulative_fees", 0.0)
            funding = port.get("cumulative_funding", 0.0)
            used_margin = port.get("used_margin", 0.0)
            open_positions = len([p for p in port.get("positions", {}).values() if p["status"] == "OPEN"])
            
            try:
                from paper_engine.portfolio import PaperPortfolio
                temp_port = PaperPortfolio("paper_portfolio.json")
                mdd = temp_port.get_max_drawdown() * 100
            except Exception as e:
                from logger import get_logger
                get_logger("dashboard").error(f"Failed to compute drawdown: {e}")
                
        except Exception as e:
            from logger import get_logger
            get_logger("dashboard").error(f"Failed to process portfolio for dashboard: {e}")
            overall = "STATE CORRUPTED" # We can't read the portfolio!'''

new_portfolio_block = '''    equity = 10000.0
    cash = 10000.0
    realized_pnl = 0.0
    unrealized_pnl = 0.0
    fees = 0.0
    funding = 0.0
    used_margin = 0.0
    open_positions = 0
    mdd = 0.0
    
    if os.path.exists("paper_portfolio.json"):
        try:
            import json
            with open("paper_portfolio.json", "r") as f:
                port = json.load(f)
            
            # Fetch recent market prices to compute true equity
            current_price = 0.0
            try:
                from data import get_candles
                df = get_candles("BTCUSDT", "1m", 1)
                if not df.empty:
                    current_price = df['close'].iloc[-1]
            except Exception as e:
                from logger import get_logger
                get_logger("dashboard").warning(f"Failed to fetch live price for equity calc: {e}")
            
            # Compute unrealized
            for pos in port.get("positions", {}).values():
                if pos['status'] == "OPEN" and current_price > 0:
                    if pos['direction'] in ["LONG", "BUY"]:
                        unrealized_pnl += (current_price - pos['entry_price']) * pos['quantity']
                    else:
                        unrealized_pnl += (pos['entry_price'] - current_price) * pos['quantity']
                        
            cash = port.get("cash", 10000.0)
            equity = cash + unrealized_pnl
            realized_pnl = port.get("realized_pnl", 0.0)
            fees = port.get("cumulative_fees", 0.0)
            funding = port.get("cumulative_funding", 0.0)
            used_margin = port.get("used_margin", 0.0)
            open_positions = len([p for p in port.get("positions", {}).values() if p["status"] == "OPEN"])
            
            try:
                from paper_engine.portfolio import PaperPortfolio
                temp_port = PaperPortfolio("paper_portfolio.json")
                mdd = temp_port.get_max_drawdown() * 100
            except Exception as e:
                from logger import get_logger
                get_logger("dashboard").error(f"Failed to compute drawdown: {e}")
                
        except Exception as e:
            from logger import get_logger
            get_logger("dashboard").error(f"Failed to process portfolio for dashboard: {e}")
            overall = "STATE CORRUPTED" # We can't read the portfolio!'''

# Replace exactly
if old_portfolio_block in dashboard_content:
    # First, remove the duplicate initialization lines above it
    dashboard_content = dashboard_content.replace('''    equity = 0.0
    cash = 0.0
    realized_pnl = 0.0
    unrealized_pnl = 0.0
    fees = 0.0
    funding = 0.0
    used_margin = 0.0
    open_positions = 0
    mdd = 0.0

    if os.path.exists("paper_portfolio.json"):''', '    if os.path.exists("paper_portfolio.json"):')
    
    dashboard_content = dashboard_content.replace(old_portfolio_block, new_portfolio_block)
else:
    print("Could not find old_portfolio_block in dashboard.py!")


# PATCH 2: In dashboard.py /api/trades (get_trades)
# Filter by experiment_id
old_trades_block = '''        # 1. Parse closed trades from ledger
        if os.path.exists("paper_trade_ledger.jsonl"):
            with open("paper_trade_ledger.jsonl", "r") as f:
                for line in f:
                    try:
                        trade = json.loads(line)
                        pnl = trade.get("net_pnl", 0.0)
                        
                        if pnl > 0:
                            wins += 1
                            gross_profit += pnl
                        else:
                            losses += 1
                            gross_loss += abs(pnl)'''

new_trades_block = '''        # 1. Parse closed trades from ledger
        if os.path.exists("paper_trade_ledger.jsonl"):
            active_exp_id = "UNKNOWN"
            if os.path.exists("experiments/active_forward_experiment_id.txt"):
                with open("experiments/active_forward_experiment_id.txt", "r") as expf:
                    active_exp_id = expf.read().strip()

            with open("paper_trade_ledger.jsonl", "r") as f:
                for line in f:
                    try:
                        trade = json.loads(line)
                        trade_exp_id = trade.get("experiment_id", "LEGACY_UNASSIGNED")
                        
                        # EXCLUDE LEGACY DATA
                        if trade_exp_id != active_exp_id:
                            continue
                        
                        pnl = trade.get("net_pnl", 0.0)
                        
                        if pnl > 0:
                            wins += 1
                            gross_profit += pnl
                        elif pnl < 0:
                            losses += 1
                            gross_loss += abs(pnl)'''

if old_trades_block in dashboard_content:
    dashboard_content = dashboard_content.replace(old_trades_block, new_trades_block)
else:
    print("Could not find old_trades_block in dashboard.py!")

# Also fix the profit factor display to N/A if 0 closed trades
old_pf_block = '''        total_closed = wins + losses
        win_rate = (wins / total_closed * 100) if total_closed > 0 else 0
        profit_factor = (gross_profit / gross_loss) if gross_loss > 0 else ("Infinity" if gross_profit > 0 else 0)'''

new_pf_block = '''        total_closed = wins + losses
        win_rate = (wins / total_closed * 100) if total_closed > 0 else "N/A"
        profit_factor = (gross_profit / gross_loss) if gross_loss > 0 else ("Infinity" if gross_profit > 0 else ("N/A" if total_closed == 0 else 0))'''

if old_pf_block in dashboard_content:
    dashboard_content = dashboard_content.replace(old_pf_block, new_pf_block)
else:
    print("Could not find old_pf_block in dashboard.py!")


with open(dashboard_file, 'w', encoding='utf-8') as f:
    f.write(dashboard_content)


# PATCH 3: Modify index.html
with open(index_file, 'r', encoding='utf-8') as f:
    index_content = f.read()

header_html = '''        <div class="metrics-grid" style="display: flex; flex-wrap: wrap; gap: 10px; padding: 10px; background: #1e222d; border-radius: 5px; margin-top: 10px; font-size: 0.9em;">
            <div style="width: 100%; border-bottom: 1px solid #333; padding-bottom: 10px; margin-bottom: 5px; color: #aaa;">
                <h3 style="margin:0; color:#fff;">FORWARD EXPERIMENT</h3>
                <div>Experiment ID: <strong>4ba0d007-429c-46fc-8fe6-d1dc1d0c37d8</strong></div>
                <div>Status: <strong style="color: #4CAF50;">RUNNING</strong> | Mode: <strong>PAPER</strong></div>
                <div>Start: 2026-08-15T08:56:18Z | Planned End: 2026-09-14T08:56:18Z</div>
                <div style="margin-top:5px; padding:5px; background:rgba(255,193,7,0.2); border-left:3px solid #ffc107; font-size:0.85em; color:#ffd54f;">
                    <strong>CURRENT EXPERIMENT DATA ONLY</strong><br/>
                    Legacy data excluded from current experiment metrics.
                </div>
            </div>'''

if '<div class="metrics-grid" style="display: flex; flex-wrap: wrap; gap: 10px; padding: 10px; background: #1e222d; border-radius: 5px; margin-top: 10px; font-size: 0.9em;">' in index_content:
    index_content = index_content.replace('<div class="metrics-grid" style="display: flex; flex-wrap: wrap; gap: 10px; padding: 10px; background: #1e222d; border-radius: 5px; margin-top: 10px; font-size: 0.9em;">', header_html)
else:
    print("Could not find metrics-grid header in index.html!")

with open(index_file, 'w', encoding='utf-8') as f:
    f.write(index_content)

print("Patch applied to dashboard.py and index.html successfully.")
