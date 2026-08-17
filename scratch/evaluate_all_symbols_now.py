"""
scratch/evaluate_all_symbols_now.py
Evaluates all discovered symbols on Binance Testnet 4H candles with exact feature logging.
"""

import sys
import os
import pandas as pd
from binance.client import Client

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from strategy_adx_ema import add_features, get_signal, SignalResult, _STRATEGY_TYPE, _OOS_WIN_RATE_PRIOR, _RR_RATIO
from testnet_engine.service import SymbolDiscoveryService

def evaluate_all():
    print("==================================================================")
    print("LIVE AUDIT: CURRENT 4H CANDLE EVALUATION ACROSS ALL ASSETS")
    print("==================================================================\n")
    
    discovery = SymbolDiscoveryService()
    eligible_symbols = discovery.discover_eligible_symbols(min_quote_volume=1_000_000)
    symbol_list = list(eligible_symbols.keys())
    print(f"Discovered {len(symbol_list)} eligible symbols on Testnet: {symbol_list}\n")
    
    client = Client("", "", testnet=True)
    
    for sym in symbol_list:
        klines = client.get_klines(symbol=sym, interval="4h", limit=100)
        if not klines or len(klines) < 20:
            print(f"[{sym}] INSUFFICIENT DATA (Only {len(klines)} candles returned)")
            continue
            
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
        
        fdf = add_features(df.copy())
        last = fdf.iloc[-1]
        prev = fdf.iloc[-2]
        
        close = last['close']
        ema20 = last['ema_20']
        ema50 = last['ema_50']
        ema200 = last['ema_200']
        adx = last['adx']
        atr = last['atr_adx_ema']
        atr_pct = (atr / close) * 100 if close > 0 else 0.0
        
        cross_up = (last['ema_20'] > last['ema_50']) and (prev['ema_20'] <= prev['ema_50'])
        cross_dn = (last['ema_20'] < last['ema_50']) and (prev['ema_20'] >= prev['ema_50'])
        trend_strong = adx > 25
        trend_dir = "BULLISH (Close > EMA200)" if close > ema200 else "BEARISH (Close < EMA200)"
        
        reasons = []
        if not (cross_up or cross_dn):
            rel = "EMA20 < EMA50 (No Cross)" if ema20 < ema50 else "EMA20 > EMA50 (No Cross)"
            reasons.append(f"NO_CROSSOVER ({rel})")
        if not trend_strong:
            reasons.append(f"ADX_BELOW_THRESHOLD (ADX {adx:.1f} <= 25)")
        if cross_up and close <= ema200:
            reasons.append(f"TREND_MISMATCH (Close {close:.2f} <= EMA200 {ema200:.2f})")
        if cross_dn and close >= ema200:
            reasons.append(f"TREND_MISMATCH (Close {close:.2f} >= EMA200 {ema200:.2f})")
            
        sig = get_signal(fdf)
        decision = sig.side or "HOLD"
        reason_str = "; ".join(reasons) if not sig.side else "VALID_SIGNAL_TRIGGERED"
        
        print(f"--- {sym} 4H Evaluation ---")
        print(f"  Candle Time  : {last.name}")
        print(f"  Price        : {close:.4f}")
        print(f"  EMA20 / 50   : {ema20:.4f} / {ema50:.4f}")
        print(f"  EMA200       : {ema200:.4f} | Trend Direction: {trend_dir}")
        print(f"  ADX (14)     : {adx:.2f} (Filter: > 25)")
        print(f"  ATR (14)     : {atr:.4f} (ATR%: {atr_pct:.3f}%)")
        print(f"  Decision     : {decision}")
        print(f"  Reason       : {reason_str}\n")

if __name__ == "__main__":
    evaluate_all()
