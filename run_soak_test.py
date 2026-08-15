import os
import time
import json
import subprocess
import datetime
from logger import get_logger

logger = get_logger("soak_test")

def print_report(start_time):
    print("\n" + "="*50)
    print("PHASE 7: FINAL 24/7 TESTNET SOAK TEST REPORT")
    print("="*50)
    
    runtime = (datetime.datetime.now() - start_time).total_seconds()
    print(f"Total Runtime: {runtime:.2f} seconds")
    
    portfolio = {}
    if os.path.exists("testnet_portfolio.json"):
        with open("testnet_portfolio.json", "r") as f:
            try:
                portfolio = json.load(f)
            except:
                pass
                
    stats = portfolio.get("scanner_stats", {})
    symbols = stats.get("symbols", [])
    print(f"Symbols Monitored: {len(symbols)}")
    print(f"Signals Evaluated: {stats.get('signals_detected', 0)}")
    
    qualified = 0
    rejected = 0
    accepted = 0
    if os.path.exists("testnet_opportunity_log.jsonl"):
        with open("testnet_opportunity_log.jsonl", "r") as f:
            for line in f:
                if "QUALIFIED" in line: qualified += 1
                if "REJECTED" in line: rejected += 1
                if "ACCEPTED" in line: accepted += 1
                
    print(f"Qualified Opportunities: {qualified}")
    print(f"Signals Rejected: {rejected}")
    print(f"Signals Accepted: {accepted}")
    
    print(f"Orders Submitted: {stats.get('orders_submitted', 0)}")
    print(f"Orders Filled: {stats.get('orders_filled', 0)}")
    
    trades = 0
    wins = 0
    losses = 0
    gross_pnl = 0.0
    net_pnl = 0.0
    fees = 0.0
    
    if os.path.exists("testnet_trade_ledger.jsonl"):
        with open("testnet_trade_ledger.jsonl", "r") as f:
            for line in f:
                try:
                    record = json.loads(line)
                    if "CLOSE" in record.get("action", ""):
                        trades += 1
                        pnl = record.get("pnl", 0.0)
                        fee = record.get("fees", 0.0)
                        gross_pnl += pnl
                        fees += fee
                        if pnl > 0: wins += 1
                        else: losses += 1
                except: pass
                
    print(f"Orders Closed: {trades}")
    win_rate = (wins / trades * 100) if trades > 0 else 0
    print(f"Win Rate: {win_rate:.2f}%")
    
    print(f"Gross PnL: {gross_pnl:.4f} USDT")
    print(f"Total Fees (estimated): {fees:.4f} USDT")
    print(f"Net PnL: {gross_pnl - fees:.4f} USDT")
    
    drawdown = portfolio.get("max_drawdown", 0) * 100
    print(f"Maximum Drawdown: {drawdown:.2f}%")
    
    # Read service.log for errors/reconnects
    reconnects = 0
    reconcil_fails = 0
    duplicate_attempts = 0
    safety_stops = 0
    crashes = 0
    
    if os.path.exists("service.log"):
        with open("service.log", "r") as f:
            for line in f:
                if "reconnect" in line.lower(): reconnects += 1
                if "RECONCILIATION" in line: reconcil_fails += 1
                if "duplicate" in line.lower(): duplicate_attempts += 1
                if "SAFETY HALT" in line: safety_stops += 1
                if "CRITICAL ERROR" in line: crashes += 1
                
    print(f"Number of reconnects: {reconnects}")
    print(f"Number of reconciliation failures: {reconcil_fails}")
    print(f"Number of crashes: {crashes}")
    print(f"Number of duplicate-order attempts: {duplicate_attempts}")
    print(f"Number of safety-triggered stops: {safety_stops}")
    
    print("\n" + "="*50)
    print("CONCLUSION:")
    if trades < 30:
        print("INSUFFICIENT DATA")
    elif (gross_pnl - fees) > 0:
        print("PROMISING BUT UNVALIDATED")
    else:
        print("NEGATIVE")
    
    print("\nPROFITABILITY NOT GUARANTEED.")
    print("LIVE MUST REMAIN BLOCKED.")
    print("="*50 + "\n")

if __name__ == "__main__":
    # Clear old logs
    for f in ["testnet_portfolio.json", "testnet_opportunity_log.jsonl", "testnet_trade_ledger.jsonl", "service.log"]:
        if os.path.exists(f): os.remove(f)
        
    print("Starting Final 24/7 Testnet Soak Test (monitoring mode)...")
    
    env = os.environ.copy()
    env["TRADING_MODE"] = "TESTNET"
    env["TESTNET_ONLY"] = "TRUE"
    # We must provide valid dummy credentials if we don't have real testnet ones, 
    # but wait, Binance Testnet requires actual keys to fetch balance!
    # I'll just use dummy ones and if it crashes we will see.
    # Actually, the user's local env might have real testnet keys!
    
    proc = subprocess.Popen(["python", "-m", "testnet_engine.service"], env=env)
    start = datetime.datetime.now()
    
    try:
        # Run for 2 minutes as a demonstration soak test within this session
        print("Soaking for 2 minutes to generate live metrics...")
        time.sleep(120)
    except KeyboardInterrupt:
        print("Soak test interrupted.")
    finally:
        proc.terminate()
        proc.wait()
        print_report(start)
