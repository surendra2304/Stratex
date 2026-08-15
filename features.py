import pandas as pd
import numpy as np

def add_features(df):
    """
    Computes purely backward-looking features for ML and Strategy analysis.
    Guarantees no look-ahead bias by only using shift() where appropriate
    and standard rolling windows.
    """
    df = df.copy()
    
    # --- Price Features ---
    df['returns'] = df['close'].pct_change()
    df['log_returns'] = np.log(df['close'] / df['close'].shift(1))
    df['body_size'] = abs(df['close'] - df['open']) / df['open']
    
    df['upper_wick'] = np.maximum(df['high'] - df['close'], df['high'] - df['open']) / df['open']
    df['lower_wick'] = np.maximum(df['close'] - df['low'], df['open'] - df['low']) / df['open']
    
    df['range'] = (df['high'] - df['low']) / df['open']
    
    # --- Trend Features ---
    df['ema_9'] = df['close'].ewm(span=9, adjust=False).mean()
    df['ema_21'] = df['close'].ewm(span=21, adjust=False).mean()
    df['ema_50'] = df['close'].ewm(span=50, adjust=False).mean()
    df['ema_200'] = df['close'].ewm(span=200, adjust=False).mean()
    
    df['dist_ema_21'] = (df['close'] - df['ema_21']) / df['ema_21']
    df['dist_ema_200'] = (df['close'] - df['ema_200']) / df['ema_200']
    
    df['trend_slope_21'] = df['ema_21'].pct_change(periods=5)
    
    # --- Momentum Features ---
    delta = df['close'].diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
    rs = gain / loss
    df['rsi_14'] = 100 - (100 / (1 + rs))
    
    ema_12 = df['close'].ewm(span=12, adjust=False).mean()
    ema_26 = df['close'].ewm(span=26, adjust=False).mean()
    df['macd'] = ema_12 - ema_26
    df['macd_signal'] = df['macd'].ewm(span=9, adjust=False).mean()
    df['macd_hist'] = df['macd'] - df['macd_signal']
    
    # --- Volatility Features ---
    tr1 = df['high'] - df['low']
    tr2 = abs(df['high'] - df['close'].shift(1))
    tr3 = abs(df['low'] - df['close'].shift(1))
    df['tr'] = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    df['atr_14'] = df['tr'].rolling(window=14).mean()
    df['atr'] = df['atr_14']
    df['atr_pct'] = df['atr_14'] / df['close']
    
    df['bb_middle'] = df['close'].rolling(window=20).mean()
    df['bb_std'] = df['close'].rolling(window=20).std()
    df['bb_upper'] = df['bb_middle'] + (df['bb_std'] * 2)
    df['bb_lower'] = df['bb_middle'] - (df['bb_std'] * 2)
    df['bb_width'] = (df['bb_upper'] - df['bb_lower']) / df['bb_middle']
    
    # Position inside Bollinger Bands (0 = at lower band, 1 = at upper band)
    df['bb_pos'] = (df['close'] - df['bb_lower']) / (df['bb_upper'] - df['bb_lower'] + 1e-9)
    
    # --- Supertrend Features ---
    # Requires ATR which is already calculated as 'atr_14'
    # Actually standard Supertrend uses ATR(10) and multiplier 3.0
    df['hl2'] = (df['high'] + df['low']) / 2
    tr = pd.concat([df['high'] - df['low'], abs(df['high'] - df['close'].shift(1)), abs(df['low'] - df['close'].shift(1))], axis=1).max(axis=1)
    atr_10 = tr.ewm(alpha=1/10, adjust=False).mean()
    
    multiplier = 3.0
    final_upperband = df['hl2'] + (multiplier * atr_10)
    final_lowerband = df['hl2'] - (multiplier * atr_10)
    
    supertrend = [True] * len(df)
    
    # Optimization: using numpy arrays instead of pandas iloc for the loop
    close_arr = np.array(df['close'].values)
    ub_arr = np.array(final_upperband.values)
    lb_arr = np.array(final_lowerband.values)
    
    for i in range(1, len(df)):
        if close_arr[i] > ub_arr[i-1]:
            supertrend[i] = True
        elif close_arr[i] < lb_arr[i-1]:
            supertrend[i] = False
        else:
            supertrend[i] = supertrend[i-1]
            if supertrend[i] == True and lb_arr[i] < lb_arr[i-1]:
                lb_arr[i] = lb_arr[i-1]
            if supertrend[i] == False and ub_arr[i] > ub_arr[i-1]:
                ub_arr[i] = ub_arr[i-1]
                
    df['supertrend'] = supertrend
    df['st_upper'] = final_upperband
    df['st_lower'] = final_lowerband
    
    # --- Volume Features ---
    df['vol_sma_20'] = df['volume'].rolling(window=20).mean()
    df['rel_volume'] = df['volume'] / (df['vol_sma_20'] + 1e-9)
    
    # Provided from data fetching
    if 'vol_delta' in df.columns:
        df['delta_sma_20'] = df['vol_delta'].rolling(window=20).mean()
        df['rel_vol_delta'] = df['vol_delta'] / (df['vol_sma_20'] + 1e-9)
        
    return df
