# ==============================================================================
# BACKTESTER.PY - Simulates strategies over historical data
# ==============================================================================
import pandas as pd
from datetime import datetime, timedelta
from binance.client import Client
from config import API_KEY, SECRET_KEY, SYMBOL, TIMEFRAME, ACTIVE_STRATEGY
from data import add_indicators

import strategy_scalper as scalper
import strategy_swing   as swing
import strategy_ml      as ml
import strategy_aggressor as aggressor

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
    df[["open","high","low","close","volume","taker_buy_base"]] = df[["open","high","low","close","volume","taker_buy_base"]].astype(float)
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms")
    
    # Calculate Volume Delta
    df["buy_vol"] = df["taker_buy_base"]
    df["sell_vol"] = df["volume"] - df["buy_vol"]
    df["vol_delta"] = df["buy_vol"] - df["sell_vol"]
    
    print(f"Downloaded {len(df)} candles.")
    return df

def run_backtest(df, strategy_name):
    """Simulates trading bar-by-bar through history."""
    print(f"Running backtest for strategy: {strategy_name.upper()}...")
    
    # Calculate indicators over the entire history at once for speed
    df = add_indicators(df)
    
    initial_balance = 10000.0
    balance = initial_balance
    position = None
    trades = []
    
    # We need a warmup period for indicators (e.g. 200 EMA needs 200 candles)
    warmup = 200
    
    for i in range(warmup, len(df)):
        # Provide the strategy with data up to the current candle
        window = df.iloc[i-100 : i+1].copy() 
        current_candle = window.iloc[-1]
        
        # Check if we are in a trade and hit SL or TP
        if position:
            if position['side'] == 'BUY':
                if current_candle['low'] <= position['sl']:
                    # SL Hit
                    loss = position['price'] - position['sl']
                    balance -= loss * position['qty']
                    trades.append({'result': 'LOSS', 'pnl': -loss * position['qty']})
                    position = None
                elif current_candle['high'] >= position['tp']:
                    # TP Hit
                    profit = position['tp'] - position['price']
                    balance += profit * position['qty']
                    trades.append({'result': 'WIN', 'pnl': profit * position['qty']})
                    position = None
            elif position['side'] == 'SELL':
                if current_candle['high'] >= position['sl']:
                    # SL Hit
                    loss = position['sl'] - position['price']
                    balance -= loss * position['qty']
                    trades.append({'result': 'LOSS', 'pnl': -loss * position['qty']})
                    position = None
                elif current_candle['low'] <= position['tp']:
                    # TP Hit
                    profit = position['price'] - position['tp']
                    balance += profit * position['qty']
                    trades.append({'result': 'WIN', 'pnl': profit * position['qty']})
                    position = None
            continue # Can't open a new trade while one is active
            
        # If not in a trade, look for a signal
        signal = None
        sl = None
        tp = None
        
        if strategy_name == "scalper":
            signal, sl, tp = scalper.get_signal(window)
        elif strategy_name == "swing":
            signal, sl, tp = swing.get_signal(window)
        elif strategy_name == "ml":
            signal, sl, tp = ml.get_signal(window)
        elif strategy_name == "aggressor":
            signal, sl, tp = aggressor.get_signal(window)
        elif strategy_name == "multi":
            for name, strat in [("SCALPER", scalper), ("SWING", swing), ("ML", ml), ("AGGRESSOR", aggressor)]:
                sig, sl_temp, tp_temp = strat.get_signal(window)
                if sig:
                    signal, sl, tp = sig, sl_temp, tp_temp
                    break
            
        if signal:
            # Fixed trade qty of 1 BTC equivalent for simple math, or based on config
            qty = 0.001 
            position = {
                'side': signal,
                'price': current_candle['close'],
                'sl': sl,
                'tp': tp,
                'qty': qty
            }

    # Calculate metrics
    wins = [t for t in trades if t['result'] == 'WIN']
    losses = [t for t in trades if t['result'] == 'LOSS']
    win_rate = (len(wins) / len(trades)) * 100 if trades else 0
    net_profit = balance - initial_balance
    
    print("=" * 40)
    print(" BACKTEST RESULTS")
    print("=" * 40)
    print(f" Strategy    : {strategy_name.upper()}")
    print(f" Total Trades: {len(trades)}")
    print(f" Wins        : {len(wins)}")
    print(f" Losses      : {len(losses)}")
    print(f" Win Rate    : {win_rate:.2f}%")
    print(f" Net PnL     : ${net_profit:.2f}")
    print(f" Final Bal   : ${balance:.2f}")
    print("=" * 40)

if __name__ == "__main__":
    import sys
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace', line_buffering=True)
    
    data = fetch_historical_data(days=30) # Test on 30 days of 1m candles
    if data is not None and not data.empty:
        # Run backtest on active strategy from config
        run_backtest(data, ACTIVE_STRATEGY)
