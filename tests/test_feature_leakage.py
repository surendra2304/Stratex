import os
import sys

import numpy as np
import pandas as pd

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from research_phase7.research_features import build_institutional_features


def test_feature_leakage():
    """
    Part 17: Strict feature leakage testing.
    Proves that CVD, Fractional Differencing, Volatility, and Microstructure features
    are purely causal. Mutating future rows MUST NOT affect past rows.
    """
    # Create mock dataset
    np.random.seed(42)
    periods = 100
    
    df_base = pd.DataFrame({
        "timestamp": pd.date_range("2026-01-01", periods=periods, freq="1min"),
        "open": np.random.uniform(60000, 61000, periods),
        "high": np.random.uniform(61000, 62000, periods),
        "low": np.random.uniform(59000, 60000, periods),
        "close": np.random.uniform(60000, 61000, periods),
        "volume": np.random.uniform(10, 100, periods),
        "buy_vol": np.random.uniform(5, 50, periods),
        "sell_vol": np.random.uniform(5, 50, periods),
        "vol_delta": np.random.uniform(-10, 10, periods)
    })
    
    # 1. Compute features on base dataset
    df_feat_base = build_institutional_features(df_base.copy(), use_frac_diff=True, d=0.3)
    
    # Extract the features at row T=50
    target_row_idx = 50
    base_row = df_feat_base.iloc[target_row_idx].copy()
    
    # 2. Mutate future rows (T=60 to 99)
    df_mutated = df_base.copy()
    df_mutated.loc[60:, "close"] *= 2.0
    df_mutated.loc[60:, "high"] *= 2.0
    df_mutated.loc[60:, "volume"] *= 5.0
    df_mutated.loc[60:, "vol_delta"] += 100.0
    
    # 3. Compute features on mutated dataset
    df_feat_mutated = build_institutional_features(df_mutated, use_frac_diff=True, d=0.3)
    
    # Extract the features at row T=50 again
    mutated_row = df_feat_mutated.iloc[target_row_idx].copy()
    
    # 4. Assert strict equality
    ignore_cols = ["timestamp"]
    cols_to_check = [c for c in df_feat_base.columns if c not in ignore_cols]
    
    mismatches = []
    for col in cols_to_check:
        base_val = base_row[col]
        mut_val = mutated_row[col]
        
        # Check if both are NaN
        if pd.isna(base_val) and pd.isna(mut_val):
            continue
            
        if isinstance(base_val, str):
            if base_val != mut_val:
                mismatches.append(f"{col}: {base_val} != {mut_val}")
        else:
            if not np.isclose(base_val, mut_val, rtol=1e-5, atol=1e-8):
                mismatches.append(f"{col}: {base_val} != {mut_val}")
            
    if len(mismatches) > 0:
        print("FAIL: Feature Leakage Detected in the following features:")
        for m in mismatches:
            print("  -", m)
        assert False, "Feature Leakage Detected!"
    else:
        print("PASS: No Feature Leakage Detected. Pipeline is strictly causal.")

if __name__ == "__main__":
    test_feature_leakage()
