import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd
from data_client import MarketDataClient
from config import API_KEY, SECRET_KEY, TIMEFRAME, SYMBOL

def download_and_verify_data(symbol=SYMBOL, timeframe=TIMEFRAME, days=90, use_cache=True):
    """
    Part 2 & 7: Robust Data Loader
    Downloads required days of historical data, with validation for 
    missing candles, duplicates, and chronological ordering.
    Saves to a Parquet cache to speed up research.
    """
    cache_dir = "data_cache"
    os.makedirs(cache_dir, exist_ok=True)
    cache_file = os.path.join(cache_dir, f"{symbol}_{timeframe}_{days}d.parquet")
    
    if use_cache and os.path.exists(cache_file):
        print(f"[DATA LOADER] Loading {days} days of data for {symbol} from cache...")
        df = pd.read_parquet(cache_file)
        return df
        
    print(f"[DATA LOADER] Downloading {days} days of {timeframe} data for {symbol} from Binance Testnet...")
    client = MarketDataClient()
    
    if not client.is_available():
        raise ValueError(f"MarketDataClient is explicitly disabled. DATA_UNAVAILABLE.")
        
    start_str = f"{days} days ago UTC"
    raw = client.get_historical_klines(symbol, timeframe, start_str)
    
    data_source = client.data_source
    
    if not raw:
        raise ValueError(f"No data returned from Binance for {symbol}")
        
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
    
    # 1. Verify Chronological Ordering
    if not df["timestamp"].is_monotonic_increasing:
        print("[WARNING] Data is not strictly chronological! Sorting...")
        df.sort_values("timestamp", inplace=True)
        df.reset_index(drop=True, inplace=True)
        
    # 2. Check for Duplicate Timestamps
    dupes = df.duplicated(subset=["timestamp"], keep=False)
    if dupes.any():
        print(f"[WARNING] Found {dupes.sum()} duplicate timestamps! Dropping duplicates...")
        df.drop_duplicates(subset=["timestamp"], keep="last", inplace=True)
        df.reset_index(drop=True, inplace=True)
        
    # 3. Check for Missing Candles
    if timeframe == '1m':
        expected_diff = pd.Timedelta(minutes=1)
    elif timeframe == '5m':
        expected_diff = pd.Timedelta(minutes=5)
    elif timeframe == '15m':
        expected_diff = pd.Timedelta(minutes=15)
    elif timeframe == '1h':
        expected_diff = pd.Timedelta(hours=1)
    else:
        expected_diff = None
        
    if expected_diff is not None:
        diffs = df["timestamp"].diff().dropna()
        missing = (diffs != expected_diff).sum()
        if missing > 0:
            print(f"[WARNING] Detected {missing} gaps (missing candles) in the dataset.")
            # We will forward-fill missing timestamps if we wanted, but for now we just report it.
            
    # Calculate CVD base features
    df["buy_vol"] = df["taker_buy_base"]
    df["sell_vol"] = df["volume"] - df["buy_vol"]
    df["vol_delta"] = df["buy_vol"] - df["sell_vol"]
    
    # Tag data source
    df.attrs['data_source'] = data_source
    
    print(f"[DATA LOADER] Download complete. Validated {len(df)} candles. Source: {data_source}")
    
    # Save cache
    df.to_parquet(cache_file)
    print(f"[DATA LOADER] Saved to cache: {cache_file}")
    
    return df

if __name__ == "__main__":
    df = download_and_verify_data(days=90, use_cache=False)
    print(df.head())
    print(df.tail())
