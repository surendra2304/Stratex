import pandas as pd
from datetime import datetime
from binance.client import Client
from config import API_KEY, SECRET_KEY, TIMEFRAME, ACTIVE_STRATEGY
from config import BACKTEST_FEE_RATE, BACKTEST_SLIPPAGE_RATE, RISK_PER_TRADE, STARTING_BALANCE, OOS_TRAIN_PCT, OOS_VAL_PCT
from data import add_indicators

import strategy_scalper as scalper
import strategy_swing   as swing
import strategy_ml      as ml
import strategy_aggressor as aggressor

from backtest_engine import BacktestEngine
from metrics import calculate_metrics

SYMBOL = "BTCUSDT"

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
                # We rename the wrapper temporarily so the engine picks up the source
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

def split_data(df, train_pct, val_pct):
    """Splits data for Out-of-Sample testing."""
    total = len(df)
    train_end = int(total * train_pct)
    val_end = train_end + int(total * val_pct)
    
    train_df = df.iloc[:train_end].copy()
    val_df = df.iloc[train_end:val_end].copy()
    test_df = df.iloc[val_end:].copy()
    return train_df, val_df, test_df

def print_report(metrics_data, strategy_name, period_name):
    print("=" * 50)
    print(f" BACKTEST RESULTS: {strategy_name.upper()} ({period_name})")
    print("=" * 50)
    print(f" Initial Balance: ${metrics_data['initial_balance']:.2f}")
    print(f" Final Balance  : ${metrics_data['final_balance']:.2f}")
    print(f" Net PnL        : ${metrics_data['net_pnl']:.2f}")
    print(f" Return         : {metrics_data['return_pct']:.2f}%")
    print(f" Trades         : {metrics_data['total_trades']}")
    print(f" Win Rate       : {metrics_data['win_rate']:.2f}%")
    print(f" Profit Factor  : {metrics_data['profit_factor']:.2f}")
    print(f" Expectancy     : ${metrics_data['expectancy']:.2f}")
    print(f" Max Drawdown   : {metrics_data['max_dd_pct']:.2f}%")
    print(f" Sharpe Ratio   : {metrics_data['sharpe']:.2f}")
    print(f" Sortino Ratio  : {metrics_data['sortino']:.2f}")
    print(f" Calmar Ratio   : {metrics_data['calmar']:.2f}")
    print(f" Largest Win    : ${metrics_data['largest_win']:.2f}")
    print(f" Largest Loss   : ${metrics_data['largest_loss']:.2f}")
    print("=" * 50)

def run_strategy_comparison(df):
    """Runs all strategies and outputs a comparison table."""
    print("\n[COMPARISON] Running strategy comparison...\n")
    results = []
    strats_to_test = ["scalper", "swing", "ml", "aggressor", "multi"]
    
    for s_name in strats_to_test:
        strats = get_strategy_by_name(s_name)
        engine = BacktestEngine(df, strats, BACKTEST_FEE_RATE, BACKTEST_SLIPPAGE_RATE, STARTING_BALANCE, RISK_PER_TRADE)
        trades, equity = engine.run()
        metrics = calculate_metrics(trades, equity, STARTING_BALANCE)
        
        results.append({
            "Strategy": s_name.upper(),
            "Trades": metrics["total_trades"],
            "WinRate": f"{metrics['win_rate']:.1f}%",
            "PF": f"{metrics['profit_factor']:.2f}",
            "NetPnL": f"${metrics['net_pnl']:.2f}",
            "MaxDD": f"{metrics['max_dd_pct']:.1f}%",
            "Sharpe": f"{metrics['sharpe']:.2f}",
            "Exp": f"${metrics['expectancy']:.2f}"
        })
        
    res_df = pd.DataFrame(results)
    print(res_df.to_string(index=False))

def run_walk_forward(df, strategy_name):
    """Executes a simple Walk-Forward Train/Val/Test evaluation."""
    print("\n[WALK-FORWARD] Splitting data into Train/Val/Test...")
    train_df, val_df, test_df = split_data(df, OOS_TRAIN_PCT, OOS_VAL_PCT)
    print(f"  Train: {len(train_df)} bars | Val: {len(val_df)} bars | Test: {len(test_df)} bars")
    
    strats = get_strategy_by_name(strategy_name)
    
    print("\n>>> TRAINING PERIOD")
    e_train = BacktestEngine(train_df, strats, BACKTEST_FEE_RATE, BACKTEST_SLIPPAGE_RATE, STARTING_BALANCE, RISK_PER_TRADE)
    t_train, eq_train = e_train.run()
    m_train = calculate_metrics(t_train, eq_train, STARTING_BALANCE)
    print_report(m_train, strategy_name, "TRAINING")
    
    print("\n>>> VALIDATION PERIOD")
    e_val = BacktestEngine(val_df, strats, BACKTEST_FEE_RATE, BACKTEST_SLIPPAGE_RATE, STARTING_BALANCE, RISK_PER_TRADE)
    t_val, eq_val = e_val.run()
    m_val = calculate_metrics(t_val, eq_val, STARTING_BALANCE)
    print_report(m_val, strategy_name, "VALIDATION")
    
    print("\n>>> OUT-OF-SAMPLE TEST PERIOD")
    e_test = BacktestEngine(test_df, strats, BACKTEST_FEE_RATE, BACKTEST_SLIPPAGE_RATE, STARTING_BALANCE, RISK_PER_TRADE)
    t_test, eq_test = e_test.run()
    m_test = calculate_metrics(t_test, eq_test, STARTING_BALANCE)
    print_report(m_test, strategy_name, "OOS TEST")

if __name__ == "__main__":
    import sys
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace', line_buffering=True)
    
    data = fetch_historical_data(days=30)
    if data is not None and not data.empty:
        # Pre-calculate indicators on the entire dataset safely (backward-looking only)
        data = add_indicators(data)
        
        # 1. Run Strategy Comparison
        run_strategy_comparison(data)
        
        # 2. Run Walk-Forward on the Active Strategy
        run_walk_forward(data, ACTIVE_STRATEGY)
