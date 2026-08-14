import pandas as pd
import numpy as np

def calculate_fractional_difference(series, d, threshold=1e-4):
    """
    Calculates fractional differencing for a pandas series.
    series: pandas Series
    d: differencing order (e.g. 0.1, 0.3, 0.5)
    threshold: minimum weight threshold to stop window expansion.
    """
    weights = [1.0]
    k = 1
    while True:
        w = -weights[-1] * (d - k + 1) / k
        if abs(w) < threshold:
            break
        weights.append(w)
        k += 1
        
    weights = np.array(weights)[::-1] # Reverse to match dot product
    
    # We apply this weight array over rolling windows
    # However, rolling dot product is slow, so we use a list comprehension or numpy stride
    # For large datasets, a loop might be slow, but this is a research script.
    
    res = np.full(len(series), np.nan)
    window_size = len(weights)
    
    # Fast path using pandas rolling
    # The weights must be aligned with the rolling window
    if len(series) >= window_size:
        def dot_product(x):
            return np.dot(x, weights)
        res = series.rolling(window=window_size).apply(dot_product, raw=True)
        
    return res

def build_institutional_features(df, use_frac_diff=True, d=0.3):
    """
    Part 8-14: Institutional Grade Features
    Includes CVD, Order Flow proxies, Microstructure, Volatility, and Fractional Differencing.
    """
    df = df.copy()
    
    # --- Part 8: Cumulative Volume Delta (CVD) ---
    # We use taker_buy_base for true order-flow approximation.
    df['cvd_raw'] = df['vol_delta'].cumsum()
    df['cvd_sma_20'] = df['cvd_raw'].rolling(window=20).mean()
    df['cvd_slope'] = df['cvd_raw'] - df['cvd_raw'].shift(5) # 5-period slope
    df['cvd_divergence'] = np.sign(df['close'].diff(5)) != np.sign(df['cvd_slope'])
    
    # --- Part 10: Volume Features ---
    vol_sma_50 = df['volume'].rolling(window=50).mean()
    vol_std_50 = df['volume'].rolling(window=50).std()
    df['vol_zscore'] = (df['volume'] - vol_sma_50) / (vol_std_50 + 1e-9)
    df['buy_sell_ratio'] = df['buy_vol'] / (df['sell_vol'] + 1e-9)
    
    delta_sma_50 = df['vol_delta'].rolling(window=50).mean()
    delta_std_50 = df['vol_delta'].rolling(window=50).std()
    df['delta_zscore'] = (df['vol_delta'] - delta_sma_50) / (delta_std_50 + 1e-9)
    
    df['vol_shock'] = df['volume'] > (vol_sma_50 + 3 * vol_std_50)
    
    # --- Part 11: Volatility Features ---
    tr1 = df['high'] - df['low']
    tr2 = abs(df['high'] - df['close'].shift(1))
    tr3 = abs(df['low'] - df['close'].shift(1))
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    
    df['atr_20'] = tr.rolling(window=20).mean()
    df['atr_pct'] = df['atr_20'] / df['close']
    
    log_ret = np.log(df['close'] / df['close'].shift(1))
    df['realized_vol_20'] = log_ret.rolling(window=20).std() * np.sqrt(1440) # Daily annualized approx
    
    vol_sma_100 = df['realized_vol_20'].rolling(window=100).mean()
    vol_std_100 = df['realized_vol_20'].rolling(window=100).std()
    df['volatility_zscore'] = (df['realized_vol_20'] - vol_sma_100) / (vol_std_100 + 1e-9)
    
    df['vol_regime'] = np.where(df['volatility_zscore'] > 1.0, 'HIGH_VOL', 
                                np.where(df['volatility_zscore'] < -1.0, 'LOW_VOL', 'NORMAL'))
                                
    df['range_expansion'] = (tr > df['atr_20'] * 2).astype(int)
    
    # --- Part 12: Microstructure & Return Features ---
    df['ret_1'] = df['close'].pct_change(1)
    df['ret_5'] = df['close'].pct_change(5)
    df['ret_15'] = df['close'].pct_change(15)
    df['ret_accel'] = df['ret_5'] - df['ret_5'].shift(5)
    
    df['body_ratio'] = abs(df['close'] - df['open']) / (tr + 1e-9)
    df['wick_asymmetry'] = (df['high'] - np.maximum(df['open'], df['close'])) - (np.minimum(df['open'], df['close']) - df['low'])
    
    rolling_high = df['high'].rolling(20).max()
    rolling_low = df['low'].rolling(20).min()
    df['dist_to_high'] = (df['close'] - rolling_high) / df['close']
    df['dist_to_low'] = (df['close'] - rolling_low) / df['close']
    
    # --- Part 13: Fractional Differencing ---
    if use_frac_diff:
        df['close_frac_diff'] = calculate_fractional_difference(df['close'], d=d)
        df['cvd_frac_diff'] = calculate_fractional_difference(df['cvd_raw'], d=d)
        
    # Standard indicators for Ablation Group A
    df['ema_21'] = df['close'].ewm(span=21, adjust=False).mean()
    df['ema_50'] = df['close'].ewm(span=50, adjust=False).mean()
    delta = df['close'].diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
    rs = gain / (loss + 1e-9)
    df['rsi_14'] = 100 - (100 / (1 + rs))
        
    return df
