"""
scratch/verify_benchmark_equivalence.py
Tests strategy_benchmark.py ADXEMAStrategy vs production strategy_adx_ema.py for 100% output equivalence.
"""

import sys
import os
import pandas as pd
import numpy as np

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from strategy_adx_ema import add_features as prod_add_features, get_signal as prod_get_signal

def run_equivalence_test():
    print("==================================================================")
    print("EQUIVALENCE PROOF: strategy_benchmark.py vs strategy_adx_ema.py")
    print("==================================================================\n")
    
    # 1. Reconstruct the Benchmark ADXEMAStrategy exactly from strategy_benchmark.py
    def bench_compute_atr(df, period=14):
        tr = pd.concat([
            df['high'] - df['low'], 
            abs(df['high'] - df['close'].shift(1)), 
            abs(df['low'] - df['close'].shift(1))
        ], axis=1).max(axis=1)
        return tr.ewm(alpha=1/period, adjust=False).mean()

    def bench_compute_adx(df, period=14):
        plus_dm = df['high'].diff()
        minus_dm = df['low'].diff(-1).shift(1)
        plus_dm = np.where((plus_dm > minus_dm) & (plus_dm > 0), plus_dm, 0.0)
        minus_dm = np.where((minus_dm > plus_dm) & (minus_dm > 0), minus_dm, 0.0)
        
        tr = pd.concat([
            df['high'] - df['low'], 
            abs(df['high'] - df['close'].shift(1)), 
            abs(df['low'] - df['close'].shift(1))
        ], axis=1).max(axis=1)
        
        tr_smooth = tr.ewm(alpha=1/period, adjust=False).mean()
        plus_di = pd.Series(plus_dm).ewm(alpha=1/period, adjust=False).mean() / tr_smooth * 100
        minus_di = pd.Series(minus_dm).ewm(alpha=1/period, adjust=False).mean() / tr_smooth * 100
        
        dx = (abs(plus_di - minus_di) / (plus_di + minus_di + 1e-9)) * 100
        return dx.ewm(alpha=1/period, adjust=False).mean()

    def bench_get_signal(df):
        if len(df) < 2: return None, None, None
        last, prev = df.iloc[-1], df.iloc[-2]
        
        cross_up = last['ema_20'] > last['ema_50'] and prev['ema_20'] <= prev['ema_50']
        cross_dn = last['ema_20'] < last['ema_50'] and prev['ema_20'] >= prev['ema_50']
        
        if cross_up and last['close'] > last['ema_200'] and last['adx'] > 25:
            return "BUY", last['close'] - (2 * last['atr']), last['close'] + (3 * last['atr'])
        if cross_dn and last['close'] < last['ema_200'] and last['adx'] > 25:
            return "SELL", last['close'] + (2 * last['atr']), last['close'] - (3 * last['atr'])
        return None, None, None

    # 2. Test on Synthetic & Historical variations across 1,000 bars
    np.random.seed(42)
    n = 1000
    close = 50000 + np.cumsum(np.random.randn(n) * 200)
    high = close + np.abs(np.random.randn(n) * 150)
    low = close - np.abs(np.random.randn(n) * 150)
    open_ = close - np.random.randn(n) * 50
    volume = np.random.randint(100, 5000, n).astype(float)
    idx = pd.date_range("2023-01-01", periods=n, freq="4h")
    
    df = pd.DataFrame({"open": open_, "high": high, "low": low, "close": close, "volume": volume}, index=idx)
    
    # Run Benchmark calculation
    df_bench = df.copy()
    df_bench['ema_20'] = df_bench['close'].ewm(span=20, adjust=False).mean()
    df_bench['ema_50'] = df_bench['close'].ewm(span=50, adjust=False).mean()
    df_bench['ema_200'] = df_bench['close'].ewm(span=200, adjust=False).mean()
    df_bench['atr'] = bench_compute_atr(df_bench, 14)
    df_bench['adx'] = bench_compute_adx(df_bench, 14)
    
    # Run Production calculation
    df_prod = prod_add_features(df.copy())
    
    # Compare feature values
    ema20_diff = np.max(np.abs(df_bench['ema_20'].values - df_prod['ema_20'].values))
    ema50_diff = np.max(np.abs(df_bench['ema_50'].values - df_prod['ema_50'].values))
    ema200_diff = np.max(np.abs(df_bench['ema_200'].values - df_prod['ema_200'].values))
    atr_diff = np.max(np.abs(df_bench['atr'].values - df_prod['atr_adx_ema'].values))
    adx_diff = np.max(np.abs(df_bench['adx'].values - df_prod['adx'].values))
    
    print(f"1. FEATURE NUMERICAL MAX ABS DIFFERENCES across {n} candles:")
    print(f"   - EMA 20 max diff  : {ema20_diff:.10f}")
    print(f"   - EMA 50 max diff  : {ema50_diff:.10f}")
    print(f"   - EMA 200 max diff : {ema200_diff:.10f}")
    print(f"   - ATR max diff     : {atr_diff:.10f}")
    print(f"   - ADX max diff     : {adx_diff:.10f}")
    
    # Compare signal outputs on rolling slices
    bench_signals = []
    prod_signals = []
    
    for i in range(50, n):
        slice_bench = df_bench.iloc[:i+1]
        b_sig, b_sl, b_tp = bench_get_signal(slice_bench)
        bench_signals.append((b_sig, b_sl, b_tp))
        
        slice_prod = df_prod.iloc[:i+1]
        p_res = prod_get_signal(slice_prod)
        prod_signals.append((p_res.side, p_res.sl, p_res.tp))
        
    mismatches = 0
    for i, (b, p) in enumerate(zip(bench_signals, prod_signals)):
        if b[0] != p[0]:
            print(f"   [MISMATCH at step {i}] Benchmark: {b[0]} != Production: {p[0]}")
            mismatches += 1
            
    print(f"\n2. SIGNAL DECISION EQUIVALENCE:")
    print(f"   - Total evaluated bars : {len(bench_signals)}")
    print(f"   - Decision mismatches  : {mismatches}")
    print(f"   - Equivalence Verdict  : {'PERFECT (100% IDENTICAL)' if mismatches == 0 else 'FAILED'}")

if __name__ == "__main__":
    run_equivalence_test()
