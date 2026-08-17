"""
scratch/test_resample_4h.py
Test fetching 1000 1h candles from Testnet and aggregating to 250 4h candles.
"""

import sys
import os
import pandas as pd
from binance.client import Client

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from strategy_adx_ema import add_features, get_signal

def test_fetch_and_eval():
    client = Client("", "", testnet=True)
    klines = client.get_klines(symbol="BTCUSDT", interval="1h", limit=1000)
    print(f"Fetched {len(klines)} 1h candles from Testnet.")
    
    df = pd.DataFrame(klines, columns=[
        'timestamp', 'open', 'high', 'low', 'close', 'volume',
        'close_time', 'quote_asset_volume', 'number_of_trades',
        'taker_buy_base_asset_volume', 'taker_buy_quote_asset_volume', 'ignore'
    ])
    df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
    for col in ['open', 'high', 'low', 'close', 'volume', 'taker_buy_base_asset_volume']:
        df[col] = df[col].astype(float)
        
    df['buy_vol'] = df['taker_buy_base_asset_volume']
    df['sell_vol'] = df['volume'] - df['buy_vol']
    df['vol_delta'] = df['buy_vol'] - df['sell_vol']
    df.set_index('timestamp', inplace=True)
    
    df_4h = df.resample('4h').agg({
        'open': 'first',
        'high': 'max',
        'low': 'min',
        'close': 'last',
        'volume': 'sum',
        'vol_delta': 'sum',
        'buy_vol': 'sum',
        'sell_vol': 'sum'
    }).dropna()
    
    print(f"Resampled to {len(df_4h)} 4h candles.")
    df_4h.reset_index(inplace=True)
    
    fdf = add_features(df_4h.copy())
    print("Features added successfully! Total rows:", len(fdf))
    
    last = fdf.iloc[-1]
    print(f"Latest 4h Candle: {last['timestamp']} | Close: {last['close']} | EMA20: {last['ema_20']:.2f} | EMA50: {last['ema_50']:.2f} | EMA200: {last['ema_200']:.2f} | ADX: {last['adx']:.2f} | ATR: {last['atr_adx_ema']:.2f}")
    
    sig = get_signal(fdf)
    print(f"Strategy Evaluation Signal: {sig}")

if __name__ == "__main__":
    test_fetch_and_eval()
