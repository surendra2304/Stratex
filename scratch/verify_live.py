import requests
import time
import json
from datetime import datetime

BASE_URL = "https://algorithmic-trading-bot-fra.onrender.com"

endpoints = {
    "health": f"{BASE_URL}/health",
    "engine_health": f"{BASE_URL}/api/engine-health",
    "status": f"{BASE_URL}/api/status",
    "scanner": f"{BASE_URL}/api/scanner",
    "trades": f"{BASE_URL}/api/trades"
}

print("=== INITIAL FETCH ===")
initial_data = {}
for name, url in endpoints.items():
    try:
        resp = requests.get(url, timeout=10)
        print(f"[{resp.status_code}] {name}: {url}")
        if resp.status_code == 200:
            initial_data[name] = resp.json()
        else:
            print(f"ERROR: {resp.text}")
    except Exception as e:
        print(f"ERROR on {name}: {e}")

print("\nWaiting 20 seconds to observe progression...")
time.sleep(20)

print("\n=== SECOND FETCH ===")
second_data = {}
for name, url in endpoints.items():
    try:
        resp = requests.get(url, timeout=10)
        if resp.status_code == 200:
            second_data[name] = resp.json()
    except Exception as e:
        print(f"ERROR on {name}: {e}")

print("\n=== ANALYSIS ===")
# Engine Health
ih1 = initial_data.get("engine_health", {})
ih2 = second_data.get("engine_health", {})
print(f"Engine Status: {ih2.get('engine_status')}")
print(f"Candle eval advanced: {ih1.get('last_evaluation_time')} -> {ih2.get('last_evaluation_time')} ({ih1.get('last_evaluation_time') != ih2.get('last_evaluation_time')})")

# Scanner
sc1 = initial_data.get("scanner", {})
sc2 = second_data.get("scanner", {})
print(f"Timeframes 1: {list(sc1.get('timeframe_metrics', {}).keys()) if 'timeframe_metrics' in sc1 else []}")
print(f"Strategies 1: {list(sc1.get('strategy_metrics', {}).keys()) if 'strategy_metrics' in sc1 else []}")
print(f"Total Signals: {sc1.get('TOTAL_SIGNALS')} -> {sc2.get('TOTAL_SIGNALS')}")
if sc2.get('TOTAL_SIGNALS', 0) > 0:
    print(f"Profitability Rejected: {sc2.get('PROFITABILITY_REJECTED')}")
    print(f"Risk Rejected: {sc2.get('RISK_REJECTED')}")
    print(f"Orders Filled: {sc2.get('ORDERS_FILLED')}")
    
# Status
st1 = initial_data.get("status", {})
st2 = second_data.get("status", {})
print(f"Components 2: {st2.get('components', {})}")
print(f"Realized PnL: {st2.get('realized_pnl')}")
print(f"Open Positions: {st2.get('open_positions')}")

# Trades
tr2 = second_data.get("trades", {})
print(f"Positions count: {len(tr2.get('positions', []))}")
