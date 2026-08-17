import pandas as pd
import numpy as np

def classify_regimes(df):
    """
    Classifies the market regime for every row in the DataFrame.
    Uses strictly backward-looking features.
    
    Returns the DataFrame with new columns:
    - regime: 'TREND_UP', 'TREND_DOWN', 'RANGE'
    - volatility_state: 'HIGH_VOL', 'LOW_VOL'
    """
    if df is None or df.empty:
        return df

    df = df.copy()
    
    # Calculate required features if not already present
    if 'ema_50' not in df.columns:
        df['ema_50'] = df['close'].ewm(span=50, adjust=False).mean()
    if 'ema_200' not in df.columns:
        df['ema_200'] = df['close'].ewm(span=200, adjust=False).mean()
    if 'bb_width' not in df.columns:
        bb_middle = df['close'].rolling(window=20).mean()
        bb_std = df['close'].rolling(window=20).std()
        df['bb_width'] = (bb_std * 4) / (bb_middle + 1e-9)
        
    # Adaptive rolling volatility window (relative to dataset size, max 50 bars)
    vol_window = min(len(df), 50)
    df['bb_width_ma'] = df['bb_width'].rolling(window=vol_window, min_periods=1).mean()
    
    # 1. Volatility Regime
    df['volatility_state'] = np.where(df['bb_width'] > df['bb_width_ma'], 'HIGH_VOL', 'LOW_VOL')
    
    # 2. Trend Regime
    # Strong Uptrend: Price > EMA50 > EMA200
    # Strong Downtrend: Price < EMA50 < EMA200
    # Range: Everything else
    conditions = [
        (df['close'] > df['ema_50']) & (df['ema_50'] > df['ema_200']),
        (df['close'] < df['ema_50']) & (df['ema_50'] < df['ema_200'])
    ]
    choices = ['TREND_UP', 'TREND_DOWN']
    
    df['regime'] = np.select(conditions, choices, default='RANGE')
    
    return df
