import os
import sys
import pandas as pd
import importlib
import warnings

warnings.filterwarnings('ignore')

from config import ACTIVE_STRATEGIES
from testnet_engine.profitability_gate import ProfitabilityGate
from testnet_engine.risk_gate import RiskGate
from research_phase9.cost_engine import CostEngine
from data import get_candles, add_indicators

def run_replay_audit():
    print("=== HISTORICAL SIGNAL REPLAY AUDIT ===")
    
    symbol = "LINKUSDT"
    timeframes = ["15m", "1h"]
    limit = 1000
    
    cost_engine = CostEngine.get_binance_taker_config()
    profitability_gate = ProfitabilityGate(cost_engine=cost_engine)
    risk_gate = RiskGate(starting_balance=10000.0)
    
    stats = {
        "EVALUATIONS": 0,
        "BUY": 0,
        "SELL": 0,
        "HOLD": 0,
        "PROFITABILITY_ACCEPTED": 0,
        "PROFITABILITY_REJECTED": 0,
        "RISK_ACCEPTED": 0,
        "RISK_REJECTED": 0,
        "reasons_profit": {},
        "reasons_risk": {}
    }
    
    strategies = {}
    for strat_name in ACTIVE_STRATEGIES.keys():
        try:
            strategies[strat_name] = importlib.import_module(f"strategy_{strat_name}")
        except Exception as e:
            print(f"Failed to load {strat_name}: {e}")
            
    for tf in timeframes:
        print(f"\nFetching {limit} candles for {symbol} {tf}...")
        df = get_candles(symbol, tf, limit=limit)
        if df is None or df.empty:
            print("Failed to fetch data.")
            continue
            
        df = add_indicators(df)
        
        # Replay candle by candle
        for i in range(50, len(df)):
            window = df.iloc[:i+1]
            current_price = float(window['close'].iloc[-1])
            stats["EVALUATIONS"] += len(strategies)
            
            for strat_name, strat_mod in strategies.items():
                try:
                    signal_result = strat_mod.get_signal(window)
                except Exception as e:
                    continue
                    
                side = getattr(signal_result, 'side', signal_result[0] if signal_result else None)
                sl = getattr(signal_result, 'sl', signal_result[1] if signal_result else None)
                tp = getattr(signal_result, 'tp', signal_result[2] if signal_result else None)
                
                if not side:
                    stats["HOLD"] += 1
                    continue
                    
                if side == "SELL":
                    stats["SELL"] += 1
                    continue
                    
                if side == "BUY":
                    stats["BUY"] += 1
                    
                    passed_profit, metrics = profitability_gate.evaluate_signal(
                        symbol, side, current_price, sl, tp, signal_result
                    )
                    
                    if not passed_profit:
                        stats["PROFITABILITY_REJECTED"] += 1
                        reason = metrics.get('reason', 'UNKNOWN')
                        stats["reasons_profit"][reason] = stats["reasons_profit"].get(reason, 0) + 1
                        
                        # Print an example to see exact math
                        if stats["PROFITABILITY_REJECTED"] % 50 == 1:
                            print(f"\n[PROFIT REJECT EXAMPLE] {strat_name} {tf}")
                            print(f"Price: {current_price}, SL: {sl}, TP: {tp}")
                            for k, v in metrics.items():
                                print(f"  {k}: {v}")
                                
                        continue
                        
                    stats["PROFITABILITY_ACCEPTED"] += 1
                    
                    # Risk Gate
                    qty = risk_gate.calculate_position_size(10000.0, current_price, sl, {"stepSize": 0.01, "minNotional": 5.0})
                    passed_risk, r_reason, _ = risk_gate.evaluate_risk(
                        symbol, side, 10000.0, {}, qty, current_price, "OK"
                    )
                    
                    if not passed_risk:
                        stats["RISK_REJECTED"] += 1
                        stats["reasons_risk"][r_reason] = stats["reasons_risk"].get(r_reason, 0) + 1
                        continue
                        
                    stats["RISK_ACCEPTED"] += 1

    print("\n=== REPLAY RESULTS ===")
    for k, v in stats.items():
        if not isinstance(v, dict):
            print(f"{k}: {v}")
            
    print("\nProfitability Reject Reasons:")
    for k, v in stats["reasons_profit"].items():
        print(f"  {k}: {v}")
        
    print("\nRisk Reject Reasons:")
    for k, v in stats["reasons_risk"].items():
        print(f"  {k}: {v}")

if __name__ == "__main__":
    run_replay_audit()
