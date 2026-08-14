import pandas as pd
import numpy as np

def resample_timeframe(df_1m, target_tf):
    """
    Part 3 & 4: Correct Timeframe Resampling
    Aggregates 1-minute OHLCV and volume delta data mathematically 
    into higher timeframes (5m, 15m, 1h, etc) to avoid repeated API calls.
    """
    if target_tf == '1m':
        return df_1m.copy()
        
    # Map timeframe strings to pandas freq
    tf_map = {
        '5m': '5min',
        '15m': '15min',
        '30m': '30min',
        '1h': '1h',
        '4h': '4h'
    }
    
    if target_tf not in tf_map:
        raise ValueError(f"Unsupported timeframe: {target_tf}")
        
    freq = tf_map[target_tf]
    
    # Ensure timestamp is index for resampling
    df = df_1m.copy()
    df.set_index("timestamp", inplace=True)
    
    # Define aggregation rules
    agg_dict = {
        'open': 'first',
        'high': 'max',
        'low': 'min',
        'close': 'last',
        'volume': 'sum'
    }
    
    if 'taker_buy_base' in df.columns:
        agg_dict['taker_buy_base'] = 'sum'
    if 'buy_vol' in df.columns:
        agg_dict['buy_vol'] = 'sum'
    if 'sell_vol' in df.columns:
        agg_dict['sell_vol'] = 'sum'
    if 'vol_delta' in df.columns:
        agg_dict['vol_delta'] = 'sum'
        
    resampled = df.resample(freq).agg(agg_dict)
    
    # Drop rows that have NaNs (e.g. periods where no 1m candles existed)
    resampled.dropna(subset=['open', 'high', 'low', 'close'], inplace=True)
    
    resampled.reset_index(inplace=True)
    
    return resampled
