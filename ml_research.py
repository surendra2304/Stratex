import os

import numpy as np
import pandas as pd
import xgboost as xgb
from binance.client import Client
from sklearn.preprocessing import StandardScaler

from features import add_features

# Configurations
SYMBOL = "BTCUSDT"
INTERVAL = "5m"
# ~180 days of 5m data (approx 51,840 candles)
START_STR = "180 days ago UTC"

def download_historical_data():
    """Downloads historical data from Public Mainnet and caches it."""
    cache_file = f"cache_{SYMBOL}_{INTERVAL}.csv"
    if os.path.exists(cache_file):
        print(f"Loading cached data from {cache_file}...")
        df = pd.read_csv(cache_file)
        df["timestamp"] = pd.to_datetime(df["timestamp"])
        return df
        
    print(f"Downloading historical {INTERVAL} data for {SYMBOL} from {START_STR}...")
    client = Client("", "") # Public Mainnet
    
    klines = client.get_historical_klines(SYMBOL, INTERVAL, START_STR)
    if not klines:
        print("Failed to download data.")
        return pd.DataFrame()
        
    print(f"Downloaded {len(klines)} raw candles.")
    
    df = pd.DataFrame(klines, columns=[
        "timestamp", "open", "high", "low", "close", "volume",
        "close_time", "quote_volume", "trades", "taker_buy_base",
        "taker_buy_quote", "ignore"
    ])
    df = df[["timestamp", "open", "high", "low", "close", "volume", "taker_buy_base"]].copy()
    
    numeric_cols = ["open", "high", "low", "close", "volume", "taker_buy_base"]
    for col in numeric_cols:
        df[col] = pd.to_numeric(df[col], errors='coerce')
        
    df.dropna(subset=numeric_cols, inplace=True)
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms")
    
    df["buy_vol"] = df["taker_buy_base"]
    df["sell_vol"] = df["volume"] - df["buy_vol"]
    df["vol_delta"] = df["buy_vol"] - df["sell_vol"]
    
    df.to_csv(cache_file, index=False)
    return df

def create_labels(df, horizon, upper_pct, lower_pct):
    """
    Creates binary labels using a barrier simulation.
    target_buy: 1 if hits upper_pct before lower_pct.
    target_sell: 1 if hits lower_pct before upper_pct.
    """
    targets_buy = np.zeros(len(df))
    targets_sell = np.zeros(len(df))
    
    closes = df['close'].values
    highs = df['high'].values
    lows = df['low'].values
    
    for i in range(len(df) - horizon):
        entry = closes[i]
        
        buy_tp = entry * (1 + upper_pct)
        buy_sl = entry * (1 + lower_pct)
        
        sell_tp = entry * (1 - upper_pct)
        sell_sl = entry * (1 - lower_pct) # lower_pct is negative, e.g. -0.01, so 1 - (-0.01) = 1.01
        
        buy_resolved = False
        sell_resolved = False
        
        for j in range(i + 1, i + 1 + horizon):
            h = highs[j]
            l = lows[j]
            
            # Check BUY
            if not buy_resolved:
                if h >= buy_tp and l <= buy_sl:
                    buy_resolved = True # Ambiguous, leave as 0
                elif h >= buy_tp:
                    targets_buy[i] = 1
                    buy_resolved = True
                elif l <= buy_sl:
                    buy_resolved = True
            
            # Check SELL
            if not sell_resolved:
                if l <= sell_tp and h >= sell_sl:
                    sell_resolved = True # Ambiguous
                elif l <= sell_tp:
                    targets_sell[i] = 1
                    sell_resolved = True
                elif h >= sell_sl:
                    sell_resolved = True
                    
            if buy_resolved and sell_resolved:
                break
                
    # Mark the last 'horizon' rows as NaN
    targets_buy[-horizon:] = np.nan
    targets_sell[-horizon:] = np.nan
        
    return targets_buy, targets_sell

def evaluate_baselines(df, slippage_pct=0.0005, fee_pct=0.001):
    """Evaluates generic baselines on the dataset."""
    total_friction = slippage_pct + fee_pct
    
    # Baseline 1: Buy and Hold
    buy_hold_rtn = (df['close'].iloc[-1] - df['close'].iloc[0]) / df['close'].iloc[0]
    
    # Baseline 2: Always BUY (simulating opening a trade every candle and holding for 15 candles)
    # We approximate average return
    future_15 = df['close'].shift(-15)
    rtns = (future_15 - df['close']) / df['close']
    always_buy_net = rtns.mean() - total_friction
    
    # Baseline 3: Always SELL
    always_sell_net = -rtns.mean() - total_friction
    
    print("\n--- BASELINES (Per Trade Expectancy) ---")
    print(f"Buy & Hold Return: {buy_hold_rtn:.2%}")
    print(f"Always BUY Expectancy:  {always_buy_net:.4%}")
    print(f"Always SELL Expectancy: {always_sell_net:.4%}")
    return rtns

def main():
    df = download_historical_data()
    if df.empty: return
    
    print("Generating Features...")
    df = add_features(df)
    
    # Map old names
    df['rsi'] = df['rsi_14']
    df['atr'] = df['atr_14']
    df['bb_mid'] = df['bb_middle']
    
    df.dropna(inplace=True)
    df.reset_index(drop=True, inplace=True)
    print(f"Dataset ready. Shape: {df.shape}")
    
    # Evaluate Baselines
    future_rtns = evaluate_baselines(df)
    df['future_rtn_15'] = future_rtns
    
    # Optimal Asymmetric TP/SL scheme chosen from label research
    best_candidate = {"horizon": 48, "upper": 0.0100, "lower": -0.0050}
    
    print(f"\nSelected Optimal Label Scheme: {best_candidate}")
    df['target_buy'], df['target_sell'] = create_labels(df, best_candidate["horizon"], best_candidate["upper"], best_candidate["lower"])
    df.dropna(subset=['target_buy', 'target_sell'], inplace=True)
    
    # Time Series Split (Strictly Sequential)
    n = len(df)
    train_size = int(n * 0.6)
    val_size = int(n * 0.2)
    
    train_df = df.iloc[:train_size].copy()
    val_df = df.iloc[train_size:train_size+val_size].copy()
    test_df = df.iloc[train_size+val_size:].copy()
    
    print("\n--- DATASET TIMESTAMPS ---")
    print(f"TRAIN: {train_df['timestamp'].iloc[0]} to {train_df['timestamp'].iloc[-1]} ({len(train_df)} samples)")
    print(f"VAL:   {val_df['timestamp'].iloc[0]} to {val_df['timestamp'].iloc[-1]} ({len(val_df)} samples)")
    print(f"TEST:  {test_df['timestamp'].iloc[0]} to {test_df['timestamp'].iloc[-1]} ({len(test_df)} samples)")
    
    features = [
        'returns', 'body_size', 'upper_wick', 'lower_wick', 'range',
        'dist_ema_21', 'dist_ema_200', 'trend_slope_21',
        'rsi_14', 'macd_hist', 'atr_pct', 'bb_width', 'bb_pos',
        'rel_volume'
    ]
    
    X_train = train_df[features]
    y_train_buy = train_df['target_buy']
    y_train_sell = train_df['target_sell']
    
    X_val = val_df[features]
    y_val_buy = val_df['target_buy']
    y_val_sell = val_df['target_sell']
    
    X_test = test_df[features]
    y_test_buy = test_df['target_buy']
    y_test_sell = test_df['target_sell']
    
    scaler = StandardScaler()
    X_train_s = scaler.fit_transform(X_train)
    X_val_s = scaler.transform(X_val)
    X_test_s = scaler.transform(X_test)
    
    scale_buy = (len(y_train_buy) - y_train_buy.sum()) / y_train_buy.sum() if y_train_buy.sum() > 0 else 1
    scale_sell = (len(y_train_sell) - y_train_sell.sum()) / y_train_sell.sum() if y_train_sell.sum() > 0 else 1
    
    print(f"\nTraining BUY Classifier (scale_pos_weight={scale_buy:.2f})...")
    model_buy = xgb.XGBClassifier(
        n_estimators=100, learning_rate=0.05, max_depth=3,
        subsample=0.8, colsample_bytree=0.8, random_state=42,
        eval_metric='logloss', early_stopping_rounds=10,
        scale_pos_weight=scale_buy
    )
    model_buy.fit(X_train_s, y_train_buy, eval_set=[(X_val_s, y_val_buy)], verbose=False)
    
    print(f"Training SELL Classifier (scale_pos_weight={scale_sell:.2f})...")
    model_sell = xgb.XGBClassifier(
        n_estimators=100, learning_rate=0.05, max_depth=3,
        subsample=0.8, colsample_bytree=0.8, random_state=42,
        eval_metric='logloss', early_stopping_rounds=10,
        scale_pos_weight=scale_sell
    )
    model_sell.fit(X_train_s, y_train_sell, eval_set=[(X_val_s, y_val_sell)], verbose=False)
    
    print("\n--- TEST SET METRICS (Unseen Data) ---")
    y_test_prob_buy = model_buy.predict_proba(X_test_s)[:, 1]
    y_test_prob_sell = model_sell.predict_proba(X_test_s)[:, 1]
    
    print(f"Baseline Buy Accuracy:  {max(y_test_buy.mean(), 1 - y_test_buy.mean()):.2%}")
    print(f"Baseline Sell Accuracy: {max(y_test_sell.mean(), 1 - y_test_sell.mean()):.2%}")
    
    # Probability Calibration & Expectancy Backtest
    print("\n--- PROBABILITY CALIBRATION (Test Set) ---")
    test_df['prob_buy'] = y_test_prob_buy
    test_df['prob_sell'] = y_test_prob_sell
    
    friction = 0.0015 # 0.15% round trip
    bins = [0, 0.20, 0.30, 0.40, 0.50, 0.55, 0.60, 1.0]
    
    tp_pct = best_candidate["upper"]
    sl_pct = abs(best_candidate["lower"])
    
    print("\nBUY MODEL BUCKETS:")
    test_df['bucket_buy'] = pd.cut(test_df['prob_buy'], bins=bins)
    for name, group in test_df.groupby('bucket_buy', observed=False):
        if len(group) == 0: continue
        actual_win_rate = group['target_buy'].mean()
        net_expectancy = (actual_win_rate * tp_pct) - ((1 - actual_win_rate) * sl_pct) - friction
        print(f"Bucket {name}: N={len(group):<4} | Actual BUY WinRate: {actual_win_rate:.2%} | Net Edge: {net_expectancy:.4%}")
        
    print("\nSELL MODEL BUCKETS:")
    test_df['bucket_sell'] = pd.cut(test_df['prob_sell'], bins=bins)
    for name, group in test_df.groupby('bucket_sell', observed=False):
        if len(group) == 0: continue
        actual_win_rate = group['target_sell'].mean()
        net_expectancy = (actual_win_rate * tp_pct) - ((1 - actual_win_rate) * sl_pct) - friction
        print(f"Bucket {name}: N={len(group):<4} | Actual SELL WinRate: {actual_win_rate:.2%} | Net Edge: {net_expectancy:.4%}")
            
    print("\n--- WALK-FORWARD SIMULATION (Test Set) ---")
    long_trades = test_df[test_df['prob_buy'] > 0.55]
    short_trades = test_df[test_df['prob_sell'] > 0.55]
    
    # Calculate PnL structurally based on the target hit
    long_pnl = np.where(long_trades['target_buy'] == 1, tp_pct, -sl_pct) - friction
    short_pnl = np.where(short_trades['target_sell'] == 1, tp_pct, -sl_pct) - friction
    
    all_pnl = np.concatenate([long_pnl, short_pnl])
    
    if len(all_pnl) > 0:
        win_rate = np.mean(all_pnl > 0)
        gross_profit = np.sum(all_pnl[all_pnl > 0])
        gross_loss = np.abs(np.sum(all_pnl[all_pnl <= 0]))
        profit_factor = gross_profit / gross_loss if gross_loss > 0 else float('inf')
        total_net = np.sum(all_pnl)
        expectancy = np.mean(all_pnl)
        
        print(f"Total Trades Taken: {len(all_pnl)}")
        print(f"Win Rate: {win_rate:.2%}")
        print(f"Profit Factor: {profit_factor:.2f}")
        print(f"Net Expectancy per trade: {expectancy:.4%}")
        print(f"Total Cumulative Return: {total_net:.2%}")
    else:
        print("No trades triggered at these thresholds.")
        
    print("\nSaving Models and Scaler...")
    import joblib
    joblib.dump(model_buy, 'model_buy.pkl')
    joblib.dump(model_sell, 'model_sell.pkl')
    joblib.dump(scaler, 'scaler.pkl')
    print("Saved model_buy.pkl, model_sell.pkl, and scaler.pkl.")

if __name__ == "__main__":
    main()
