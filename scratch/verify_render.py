import requests
import time
import json
import sys
from datetime import datetime

BASE_URL = "https://algorithmic-trading-bot-fra.onrender.com"

print(f"[{datetime.utcnow().isoformat()}] Polling Render deployment until a strategy evaluation occurs...")

for i in range(30):
    try:
        resp = requests.get(f"{BASE_URL}/api/diagnostics", timeout=10)
        resp2 = requests.get(f"{BASE_URL}/api/scanner", timeout=10)
        
        if resp.status_code == 200 and resp2.status_code == 200:
            diag = resp.json()
            scan = resp2.json()
            
            hb = diag.get("heartbeat", {})
            eval_time = hb.get("last_strategy_evaluation")
            
            print(f"[{datetime.utcnow().isoformat()}] Status Code: {resp.status_code}")
            
            if True: # Always print once to verify the new counters
                print("\n=== LIVE PIPELINE METRICS ===")
                print(f"Heartbeat Engine OK: {hb.get('testnet_engine_alive')}")
                print(f"Scanner Stats:")
                for k in ["TOTAL_SIGNALS", "PROFITABILITY_ACCEPTED", "PROFITABILITY_REJECTED", 
                          "RISK_ACCEPTED", "RISK_REJECTED", "EXECUTION_ELIGIBLE", 
                          "EXECUTION_REJECTED", "ORDERS_SUBMITTED", "ORDERS_FAILED", 
                          "ORDERS_FILLED", "OPEN_POSITIONS", "CLOSED_TRADES", "HOLD_SIGNALS", "strategy_evaluations"]:
                    print(f"  {k}: {scan.get(k, 0)}")
                sys.exit(0)
            
            if total_sigs > 0 or eval_time is not None:
                print("\n=== EVALUATION DETECTED ===")
                print(f"Heartbeat: {json.dumps(hb, indent=2)}")
                print(f"Scanner Stats: {json.dumps(scan, indent=2)}")
                sys.exit(0)
    except Exception as e:
        print(f"Error: {e}")
        
    time.sleep(10)
    
print("Timeout waiting for evaluation.")
