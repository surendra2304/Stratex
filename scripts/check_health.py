"""
scripts/check_health.py
Production health-check script for the 24/7 Binance Testnet bot.
"""

import sys
import os
import json
import socket
import datetime

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from execution import get_exchange_client
import config

def check_health():
    print("==========================================================")
    print("  BINANCE TESTNET TRADING BOT — SYSTEM HEALTH CHECK")
    print("==========================================================")
    print(f"Timestamp UTC : {datetime.datetime.utcnow().isoformat()}Z")
    
    # 1. Check if Bot Singleton Daemon is Running
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    is_running = False
    try:
        sock.bind(("127.0.0.1", 48888))
        sock.close()
        is_running = False
    except socket.error:
        is_running = True
        
    print(f"Daemon Status : {'RUNNING (Port 48888 locked)' if is_running else 'STOPPED (Port 48888 available)'}")
    
    # 2. Check Binance Testnet Connectivity & Balance
    try:
        client = get_exchange_client()
        acc = client.get_account()
        usdt = next((b for b in acc['balances'] if b['asset'] == 'USDT'), None)
        free = float(usdt['free']) if usdt else 0.0
        locked = float(usdt['locked']) if usdt else 0.0
        total_balance = free + locked
        print(f"Binance Wallet: ${total_balance:.2f} USDT (Free: ${free:.2f}, Locked: ${locked:.2f})")
    except Exception as e:
        print(f"Binance API   : ERROR ({e})")
        
    # 3. Check Local State Files
    port_file = getattr(config, "TESTNET_PORTFOLIO_FILE", "testnet_portfolio.json")
    if os.path.exists(port_file):
        try:
            with open(port_file, "r") as f:
                port = json.load(f)
            positions = port.get("positions", {})
            realized_pnl = port.get("realized_pnl", 0.0)
            stats = port.get("scanner_stats", {})
            print(f"Realized PnL  : ${realized_pnl:.2f}")
            print(f"Open Positions: {len(positions)}")
            print(f"Signals Gen   : {stats.get('TOTAL_SIGNALS', 0)}")
            print(f"Profit Reject : {stats.get('PROFITABILITY_REJECTED', 0)}")
            print(f"Qualified     : {stats.get('QUALIFIED', 0)}")
            print(f"Orders Filled : {stats.get('ORDERS_FILLED', 0)}")
        except Exception as e:
            print(f"State Read    : ERROR ({e})")
            
    # 4. Check Engine Heartbeat
    hb_file = getattr(config, "TESTNET_HEARTBEAT_FILE", "testnet_heartbeat.json")
    if not os.path.exists(hb_file) and os.path.exists("heartbeat.json"):
        hb_file = "heartbeat.json"
    if os.path.exists(hb_file):
        try:
            with open(hb_file, "r") as f:
                hb = json.load(f)
            hb_ts = hb.get("timestamp", "")
            age_s = "N/A"
            if hb_ts:
                dt = datetime.datetime.fromisoformat(hb_ts.replace("Z", "+00:00")).replace(tzinfo=None)
                age_s = f"{(datetime.datetime.utcnow() - dt).total_seconds():.1f}s ago"
            print(f"Heartbeat Age : {age_s} (Status: {hb.get('status', 'UNKNOWN')})")
            print(f"Active Strat  : {hb.get('strategy', 'N/A')} ({hb.get('timeframe', 'N/A')})")
            print(f"Symbols Active: {hb.get('symbol_count', 0)}")
            print(f"Last Evaluation: {hb.get('last_strategy_evaluation', 'N/A')}")
        except Exception as e:
            print(f"Heartbeat Read: ERROR ({e})")
    else:
        print("Heartbeat     : NOT FOUND")

    print("==========================================================")

if __name__ == "__main__":
    check_health()
