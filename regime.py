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
    df = df.copy()
    
    # Needs some basic features if not present
    if 'ema_50' not in df.columns:
        df['ema_50'] = df['close'].ewm(span=50, adjust=False).mean()
    if 'ema_200' not in df.columns:
        df['ema_200'] = df['close'].ewm(span=200, adjust=False).mean()
    if 'bb_width' not in df.columns:
        bb_middle = df['close'].rolling(window=20).mean()
        bb_std = df['close'].rolling(window=20).std()
        df['bb_width'] = (bb_std * 4) / bb_middle
        
    # Moving average of bb_width to determine relative volatility
    df['bb_width_ma_30d'] = df['bb_width'].rolling(window=30 * 24 * 60).mean() # Approx 30 days for 1m data
    # Fallback for small datasets
    if df['bb_width_ma_30d'].isna().all():
        df['bb_width_ma_30d'] = df['bb_width'].rolling(window=24 * 60).mean()
    
    # 1. Volatility Regime
    df['volatility_state'] = np.where(df['bb_width'] > df['bb_width_ma_30d'], 'HIGH_VOL', 'LOW_VOL')
    
    # 2. Trend Regime
    # Strong Uptrend: Price > EMA50 > EMA200
    # Strong Downtrend: Price < EMA50 < EMA200
    # Range: Everything else (e.g., Price between EMAs, EMAs flat/crossed without clear price dominance)
    
    conditions = [
        (df['close'] > df['ema_50']) & (df['ema_50'] > df['ema_200']),
        (df['close'] < df['ema_50']) & (df['ema_50'] < df['ema_200'])
    ]
    choices = ['TREND_UP', 'TREND_DOWN']
    
    df['regime'] = np.select(conditions, choices, default='RANGE')
    
    return df
