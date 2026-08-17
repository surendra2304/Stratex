import os
import sys
import pandas as pd
import json
import datetime
import importlib
from binance.client import Client

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from testnet_engine.profitability_gate import ProfitabilityGate
from testnet_engine.risk_gate import RiskGate
from research_phase9.cost_engine import CostEngine
from config import ACTIVE_STRATEGIES
from features import add_features

def replay_all(symbols, timeframes=["15m", "1h", "4h"], days=60):
    client = Client()
    cost_engine = CostEngine.get_binance_taker_config()
    profitability_gate = ProfitabilityGate(cost_engine=cost_engine)
    risk_gate = RiskGate(starting_balance=10000.0)
    
    strategies = {}
    for strat_name, tfs in ACTIVE_STRATEGIES.items():
        try:
            mod = importlib.import_module(f"strategy_{strat_name}")
            tfs_list = tfs if isinstance(tfs, list) else [tfs]
            for tf in tfs_list:
                if tf not in strategies:
                    strategies[tf] = []
                strategies[tf].append((strat_name, mod))
        except Exception as e:
            print(f"Error loading {strat_name}: {e}")

    results = []

    for symbol in symbols:
        print(f"\n--- Processing {symbol} ---")
        for tf in timeframes:
            if tf not in strategies:
                continue
                
            klines = client.get_historical_klines(symbol, tf, f"{days} days ago UTC")
            if not klines:
                continue
                
            df = pd.DataFrame(klines, columns=[
                'timestamp', 'open', 'high', 'low', 'close', 'volume',
                'close_time', 'quote_asset_volume', 'number_of_trades',
                'taker_buy_base_asset_volume', 'taker_buy_quote_asset_volume', 'ignore'
            ])
            for col in ['open', 'high', 'low', 'close', 'volume', 'taker_buy_base_asset_volume']:
                df[col] = df[col].astype(float)
                
            df['buy_vol'] = df['taker_buy_base_asset_volume']
            df['sell_vol'] = df['volume'] - df['buy_vol']
            df['vol_delta'] = df['buy_vol'] - df['sell_vol']
            
            df = add_features(df)
            
            for strat_name, strat_mod in strategies[tf]:
                metrics = {
                    "evaluations": 0, "BUY": 0, "SELL": 0, "HOLD": 0,
                    "profit_accepted": 0, "profit_rejected": 0,
                    "risk_accepted": 0, "risk_rejected": 0
                }
                
                for i in range(200, len(df)):
                    window = df.iloc[:i+1]
                    metrics["evaluations"] += 1
                    
                    signal_result = strat_mod.get_signal(window)
                    side = getattr(signal_result, 'side', signal_result[0] if signal_result else None)
                    sl = getattr(signal_result, 'sl', signal_result[1] if signal_result else None)
                    tp = getattr(signal_result, 'tp', signal_result[2] if signal_result else None)
                    
                    if not side:
                        metrics["HOLD"] += 1
                        continue
                        
                    if side == "BUY": metrics["BUY"] += 1
                    if side == "SELL": metrics["SELL"] += 1
                    
                    current_price = window.iloc[-1]['close']
                    
                    if side == "SELL":
                        continue
                    
                    passed_profit, p_metrics = profitability_gate.evaluate_signal(
                        symbol, side, current_price, sl, tp, signal_result
                    )
                    
                    if passed_profit:
                        metrics["profit_accepted"] += 1
                        filters = {"minQty": 0.00001, "stepSize": 0.00001, "minNotional": 5.0}
                        qty = risk_gate.calculate_position_size(10000.0, current_price, sl, filters)
                        
                        if qty > 0:
                            passed_risk, _, _ = risk_gate.evaluate_risk(
                                symbol, side, 10000.0, {}, qty, current_price, "OK"
                            )
                            if passed_risk:
                                metrics["risk_accepted"] += 1
                            else:
                                metrics["risk_rejected"] += 1
                        else:
                            metrics["risk_rejected"] += 1
                    else:
                        metrics["profit_rejected"] += 1
                        
                results.append({
                    "Symbol": symbol,
                    "Strategy": strat_name,
                    "TF": tf,
                    "Evals": metrics["evaluations"],
                    "BUY": metrics["BUY"],
                    "SELL": metrics["SELL"],
                    "Profit_Acc": metrics["profit_accepted"],
                    "Profit_Rej": metrics["profit_rejected"],
                    "Risk_Acc": metrics["risk_accepted"],
                    "Risk_Rej": metrics["risk_rejected"]
                })
                
    print("\n=== FINAL REPLAY RESULTS (ALL SYMBOLS) ===")
    df_res = pd.DataFrame(results)
    print(df_res.to_string())
    df_res.to_csv("scratch/replay_results.csv", index=False)

if __name__ == "__main__":
    syms = ['BTCUSDT', 'ETHUSDT', 'SOLUSDT', 'BNBUSDT', 'LINKUSDT', 'TRXUSDT']
    replay_all(syms, days=30)
