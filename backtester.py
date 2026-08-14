import pandas as pd
import json
import os
from datetime import datetime
from binance.client import Client
from config import API_KEY, SECRET_KEY, TIMEFRAME, ACTIVE_STRATEGY, SYMBOL
from config import BACKTEST_FEE_RATE, BACKTEST_SLIPPAGE_RATE, RISK_PER_TRADE, STARTING_BALANCE, OOS_TRAIN_PCT, OOS_VAL_PCT
from data import add_indicators

import strategy_scalper as scalper
import strategy_swing   as swing
import strategy_ml      as ml
import strategy_aggressor as aggressor

from backtest_engine import BacktestEngine
from metrics import calculate_metrics

def fetch_historical_data(days=30):
    """Downloads historical candles from Binance."""
    print(f"Downloading {days} days of {TIMEFRAME} data for {SYMBOL}...")
    client = Client(API_KEY, SECRET_KEY, testnet=True)
    
    start_str = f"{days} days ago UTC"
    raw = client.get_historical_klines(SYMBOL, TIMEFRAME, start_str)
    
    df = pd.DataFrame(raw, columns=[
        "timestamp","open","high","low","close","volume",
        "close_time","quote_volume","trades","taker_buy_base","taker_buy_quote","ignore"
    ])
    df = df[["timestamp","open","high","low","close","volume","taker_buy_base"]].copy()
    
    numeric_cols = ["open", "high", "low", "close", "volume", "taker_buy_base"]
    for col in numeric_cols:
        df[col] = pd.to_numeric(df[col], errors='coerce')
        
    df.dropna(subset=numeric_cols, inplace=True)
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms")
    
    df["buy_vol"] = df["taker_buy_base"]
    df["sell_vol"] = df["volume"] - df["buy_vol"]
    df["vol_delta"] = df["buy_vol"] - df["sell_vol"]
    
    print(f"Downloaded {len(df)} candles.")
    return df

class MultiStrategyWrapper:
    """Wrapper that runs all strategies and returns the first signal found."""
    __name__ = "multi"
    
    def __init__(self):
        self.strats = [
            ("SCALPER", scalper),
            ("SWING", swing),
            ("ML", ml),
            ("AGGRESSOR", aggressor)
        ]
        
    def get_signal(self, df):
        for name, strat in self.strats:
            sig, sl, tp = strat.get_signal(df)
            if sig:
                self.__name__ = f"multi_{name}"
                return sig, sl, tp
        return None, None, None

def get_strategy_by_name(name):
    if name == "scalper": return [scalper]
    if name == "swing": return [swing]
    if name == "ml": return [ml]
    if name == "aggressor": return [aggressor]
    if name == "multi": return [MultiStrategyWrapper()]
    return []

def run_rolling_walk_forward(df, strategy_name, num_windows=5):
    """Executes a True Rolling Walk-Forward evaluation."""
    print(f"\n[WALK-FORWARD] Executing True Rolling Walk-Forward for {strategy_name.upper()}...")
    total_bars = len(df)
    
    # Dynamic split based on config
    train_size = int(total_bars * OOS_TRAIN_PCT)
    val_size = int(total_bars * OOS_VAL_PCT)
    # Remaining is available for testing. We divide remaining into num_windows slices
    remaining_bars = total_bars - train_size - val_size
    test_step = max(1, remaining_bars // num_windows)
    
    all_oos_trades = []
    oos_equity_frames = []
    current_equity = STARTING_BALANCE
    
    strats = get_strategy_by_name(strategy_name)
    
    for w in range(num_windows):
        start_idx = w * test_step
        if start_idx + train_size + val_size + test_step > total_bars and w == num_windows - 1:
            # Last window takes the rest
            test_end = total_bars
        else:
            test_end = start_idx + train_size + val_size + test_step
            
        if start_idx + train_size >= total_bars: break
            
        train_df = df.iloc[start_idx : start_idx+train_size].copy()
        val_df = df.iloc[start_idx+train_size : start_idx+train_size+val_size].copy()
        test_df = df.iloc[start_idx+train_size+val_size : test_end].copy()
        
        # Train models if the strategy supports it
        for strat in strats:
            if hasattr(strat, 'train'):
                print(f"    Training {strat.__name__} model on {len(train_df)} bars...")
                strat.train(train_df, val_df)
                
        engine = BacktestEngine(test_df, strats, BACKTEST_FEE_RATE, BACKTEST_SLIPPAGE_RATE, current_equity, RISK_PER_TRADE, symbol=SYMBOL)
        trades, equity = engine.run()
        
        all_oos_trades.extend(trades)
        
        if not equity.empty:
            # Stitch equity
            current_equity = equity.iloc[-1]['equity']
            oos_equity_frames.append(equity)
            
        print(f"  Window {w+1}: Train={len(train_df)}, Val={len(val_df)}, Test={len(test_df)} | Window PnL: ${current_equity - STARTING_BALANCE:.2f}")

    # Combine equity curve
    if oos_equity_frames:
        combined_equity = pd.concat(oos_equity_frames).drop_duplicates(subset=['timestamp']).reset_index(drop=True)
    else:
        combined_equity = pd.DataFrame()
        
    metrics = calculate_metrics(all_oos_trades, combined_equity, STARTING_BALANCE)
    return metrics, all_oos_trades, combined_equity

def generate_baseline(df):
    """Runs a baseline for all strategies and exports results."""
    print("\n[BASELINE] Generating Baseline Strategy Report...\n")
    strats_to_test = ["scalper", "swing", "ml", "aggressor", "multi"]
    
    results = {}
    table_data = []
    
    os.makedirs('backtest_results', exist_ok=True)
    
    for s_name in strats_to_test:
        metrics, trades, eq = run_rolling_walk_forward(df, s_name, num_windows=5)
        
        results[s_name] = metrics
        
        table_data.append({
            "Strategy": s_name.upper(),
            "Trades": metrics["total_trades"],
            "WinRate": f"{metrics['win_rate']:.1f}%",
            "PF": f"{metrics['profit_factor']:.2f}" if metrics['profit_factor'] != float('inf') else "INF",
            "NetPnL": f"${metrics['net_pnl']:.2f}",
            "MaxDD": f"{metrics['max_dd_pct']:.1f}%",
            "Sharpe": f"{metrics['sharpe']:.2f}",
            "Exp": f"${metrics['expectancy']:.2f}"
        })
        
    # JSON Export
    with open('backtest_results/baseline_results.json', 'w') as f:
        # replace inf with string "Infinity" for JSON compliance if necessary
        clean_res = {}
        for k, v in results.items():
            clean_res[k] = {m: (val if val != float('inf') else 'INF') for m, val in v.items()}
        json.dump(clean_res, f, indent=4)
        
    # MD Export
    res_df = pd.DataFrame(table_data)
    md_table = res_df.to_markdown(index=False)
    
    md_content = f"# Baseline Strategy Evaluation\n\nGenerated on: {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')} UTC\n\n## Rolling OOS Performance\n\n{md_table}\n"
    
    with open('backtest_results/baseline_report.md', 'w') as f:
        f.write(md_content)
        
    print("\nBASELINE EVALUATION RESULTS:")
    print(res_df.to_string(index=False))
    print("\nBaseline report generated in backtest_results/baseline_report.md")

def generate_improved(df):
    """Runs the improved strategies and exports results."""
    print("\n[IMPROVED] Generating Improved Strategy Report...\n")
    strats_to_test = ["scalper", "swing", "ml", "aggressor", "multi"]
    
    results = {}
    table_data = []
    
    os.makedirs('backtest_results', exist_ok=True)
    
    for s_name in strats_to_test:
        metrics, trades, eq = run_rolling_walk_forward(df, s_name, num_windows=5)
        
        results[s_name] = metrics
        
        table_data.append({
            "Strategy": s_name.upper(),
            "Trades": metrics["total_trades"],
            "WinRate": f"{metrics['win_rate']:.1f}%",
            "PF": f"{metrics['profit_factor']:.2f}" if metrics['profit_factor'] != float('inf') else "INF",
            "NetPnL": f"${metrics['net_pnl']:.2f}",
            "MaxDD": f"{metrics['max_dd_pct']:.1f}%",
            "Sharpe": f"{metrics['sharpe']:.2f}",
            "Exp": f"${metrics['expectancy']:.2f}"
        })
        
    # JSON Export
    with open('backtest_results/improved_results.json', 'w') as f:
        clean_res = {}
        for k, v in results.items():
            clean_res[k] = {m: (val if val != float('inf') else 'INF') for m, val in v.items()}
        json.dump(clean_res, f, indent=4)
        
    # MD Export
    res_df = pd.DataFrame(table_data)
    md_table = res_df.to_markdown(index=False)
    
    md_content = f"# Improved Strategy Evaluation\n\nGenerated on: {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')} UTC\n\n## Rolling OOS Performance\n\n{md_table}\n"
    
    with open('backtest_results/improved_report.md', 'w') as f:
        f.write(md_content)
        
    print("\nIMPROVED EVALUATION RESULTS:")
    print(res_df.to_string(index=False))

if __name__ == "__main__":
    import sys
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace', line_buffering=True)
    
    data = fetch_historical_data(days=30)
    if data is not None and not data.empty:
        data = add_indicators(data)
        
        # We already generated the baseline. Now we generate the improved report.
        # generate_baseline(data)
        generate_improved(data)
