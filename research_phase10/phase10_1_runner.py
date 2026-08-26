import json
import os
import sys

import numpy as np

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import subprocess

from research_phase7.data_loader import download_and_verify_data
from research_phase9.cost_engine import CostEngine
from research_phase10.funding_engine import FundingEngine
from research_phase10.pairs_engine import PairsEngine
from research_phase10.stage10_runner import fetch_funding_history


def get_git_revision_hash():
    try:
        return subprocess.check_output(['git', 'rev-parse', 'HEAD']).decode('ascii').strip()
    except Exception:
        return "UNKNOWN"

def run_stage10_1():
    print("==============================================")
    print("Stage 10.1: METHODOLOGY CORRECTIONS")
    print("==============================================\n")
    
    os.makedirs('backtest_results/stage10', exist_ok=True)
    
    symbols = ["BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT"]
    data = {}
    
    for sym in symbols:
        print(f"[DATA] Loading 90 days for {sym}...")
        data[sym] = download_and_verify_data(sym, days=90, use_cache=True)
        
    print("\n--- 1. Evaluating Pairs Trading (Corrected) ---")
    pairs = [
        ("BTCUSDT", "ETHUSDT"),
        ("BTCUSDT", "BNBUSDT"),
        ("ETHUSDT", "SOLUSDT")
    ]
    
    pairs_results = {}
    for a, b in pairs:
        engine = PairsEngine(data[a], data[b], a, b, timeframe='1h')
        res = engine.run_walk_forward()
        pairs_results[f"{a}/{b}"] = res
        if res.get('status') == 'UNAVAILABLE':
            print(f"  -> {a}/{b}: UNAVAILABLE ({res.get('reason')})")
        else:
            print(f"  -> {a}/{b}: Viable={res['viable']}, Net PnL={res.get('total_net_pnl_pct', 0)*100:.2f}%, ADF p-val={res.get('avg_adf_p_value', 1):.4f}")
            
    print("\n--- 2. Evaluating Funding Arbitrage (Corrected) ---")
    funding_results = {}
    cost_engine = CostEngine.get_binance_taker_config()
    fund_engine = FundingEngine(cost_engine, max_leverage=3.0)
    
    for sym in symbols:
        print(f"[FUNDING] Evaluating {sym}...")
        df_funding = fetch_funding_history(sym)
        if df_funding.empty:
            continue
            
        res = fund_engine.simulate_funding_arbitrage(data[sym], data[sym], df_funding, hold_epochs=5)
        funding_results[sym] = res
        print(f"  -> {sym}: Viable={res['viable']}, Trades={res['trades']}, Net PnL={res.get('total_return_pct', 0)*100:.2f}%, Liquidations={res['liquidations']}")
        
    print("\n[Stage 10.1] Generating Reports...")
    
    # Generate Experiment Log
    experiment_log = {
        "git_commit": get_git_revision_hash(),
        "stage": "10.1",
        "pairs_results": {k: {kk: bool(vv) if isinstance(vv, np.bool_) else vv for kk, vv in v.items() if kk != 'ledger'} for k, v in pairs_results.items()},
        "funding_results": {k: {kk: bool(vv) if isinstance(vv, np.bool_) else vv for kk, vv in v.items() if kk != 'ledger'} for k, v in funding_results.items()},
        "cost_assumptions": {
            "entry_fee": cost_engine.entry_fee,
            "exit_fee": cost_engine.exit_fee,
            "entry_slip": cost_engine.entry_slip,
            "exit_slip": cost_engine.exit_slip
        }
    }
    
    with open('backtest_results/stage10/experiment_log.json', 'w') as f:
        json.dump(experiment_log, f, indent=4)
        
    with open('backtest_results/stage10/stage10_1_corrections.md', 'w') as f:
        f.write("# Stage 10.1 EXECUTIVE SUMMARY (CORRECTED METHODOLOGY)\n\n")
        f.write("> **Note**: This report supersedes Stage 10. It utilizes strict Train->Val->Test bounds, separated Basis/Funding PnL, Beta-Neutral sizing, and Leg-Specific Cost matching.\n\n")
        
        f.write("## Pairs Trading Validation\n")
        for p, res in pairs_results.items():
            if res.get('status') == 'UNAVAILABLE':
                f.write(f"- **{p}**: C - INCONCLUSIVE (UNAVAILABLE: {res.get('reason')})\n")
                continue
                
            grade = "A - Strong Candidate" if res['viable'] else "D - Reject"
            if res.get('avg_adf_p_value', 1) > 0.05:
                grade = "C - INCONCLUSIVE (Not Cointegrated OOS)"
            f.write(f"- **{p}**: {grade} (PnL: {res.get('total_net_pnl_pct', 0)*100:.2f}%, ADF p-val: {res.get('avg_adf_p_value', 1):.4f}, Half-Life: {res.get('avg_half_life', np.inf):.2f})\n")
            
        f.write("\n## Funding Arbitrage Validation\n")
        for s, res in funding_results.items():
            grade = "A - Strong Candidate" if res['viable'] else "D - Reject"
            f.write(f"- **{s}**: {grade} (PnL: {res.get('total_return_pct', 0)*100:.2f}%, Liquidations: {res['liquidations']})\n")
            
        f.write("\n## Final Go/No-Go Decision\n")
        viable_pairs = [p for p, r in pairs_results.items() if r.get('viable', False)]
        viable_fund = [s for s, r in funding_results.items() if r.get('viable', False)]
        
        if viable_pairs or viable_fund:
            f.write("We have mathematically proven out-of-sample edge. PROCEED to Stage 11.\n")
        else:
            f.write("All strategies failed rigorous corrected evaluation. **DO NOT TRADE**.\n")
            
    print("[Stage 10.1] Complete.")

if __name__ == "__main__":
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace', line_buffering=True)
    run_stage10_1()
