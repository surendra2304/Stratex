# quantum/validation/splits.py
"""Chronological walk-forward split generation with anti-leakage guards."""

import pandas as pd
import numpy as np
from dataclasses import dataclass
from typing import List, Optional, Tuple

@dataclass
class WalkForwardFold:
    fold_idx: int
    train_df: pd.DataFrame
    val_df: pd.DataFrame
    test_df: pd.DataFrame
    train_start: str
    train_end: str
    val_start: str
    val_end: str
    test_start: str
    test_end: str
    train_rows: int
    val_rows: int
    test_rows: int

def generate_walk_forward_splits(
    df: pd.DataFrame,
    n_folds: int = 5,
    train_days: float = 60.0,
    val_days: float = 15.0,
    test_days: float = 15.0,
    allow_proportional_fallback: bool = False
) -> Tuple[List[WalkForwardFold], Optional[str]]:
    """
    Generates strict chronological walk-forward folds.
    If the dataset has >= 90 days, standard time-delta splits are generated.
    If the dataset is shorter than 90 days:
      - If allow_proportional_fallback is False, returns ([], error_message).
      - If allow_proportional_fallback is True, splits proportionally based on row counts
        (e.g., 60% train, 15% val, 25% test) strictly forward in time.
    """
    if df is None or df.empty or len(df) < 100:
        return [], "Dataset is empty or too small (<100 rows)."
        
    df = df.sort_values('timestamp').reset_index(drop=True)
    start_ts = df['timestamp'].min()
    end_ts = df['timestamp'].max()
    span_days = (end_ts - start_ts).total_seconds() / 86400.0
    
    folds: List[WalkForwardFold] = []
    
    # Check if we have sufficient calendar span for 90d window
    required_days = train_days + val_days + test_days
    if span_days >= required_days:
        # We can construct true calendar day windows
        step_days = (span_days - required_days) / max(1, n_folds - 1) if n_folds > 1 else 0.0
        
        for k in range(n_folds):
            f_train_start = start_ts + pd.Timedelta(days=k * step_days)
            f_train_end = f_train_start + pd.Timedelta(days=train_days)
            f_val_start = f_train_end
            f_val_end = f_val_start + pd.Timedelta(days=val_days)
            f_test_start = f_val_end
            f_test_end = f_test_start + pd.Timedelta(days=test_days)
            
            tr = df[(df['timestamp'] >= f_train_start) & (df['timestamp'] < f_train_end)].copy().reset_index(drop=True)
            va = df[(df['timestamp'] >= f_val_start) & (df['timestamp'] < f_val_end)].copy().reset_index(drop=True)
            te = df[(df['timestamp'] >= f_test_start) & (df['timestamp'] <= f_test_end)].copy().reset_index(drop=True)
            
            if len(tr) < 20 or len(va) < 5 or len(te) < 5:
                continue
                
            folds.append(WalkForwardFold(
                fold_idx=k + 1,
                train_df=tr, val_df=va, test_df=te,
                train_start=str(tr['timestamp'].min()), train_end=str(tr['timestamp'].max()),
                val_start=str(va['timestamp'].min()), val_end=str(va['timestamp'].max()),
                test_start=str(te['timestamp'].min()), test_end=str(te['timestamp'].max()),
                train_rows=len(tr), val_rows=len(va), test_rows=len(te)
            ))
        return folds, None
        
    elif allow_proportional_fallback:
        # Fallback: Proportional row-based rolling walk-forward (Strictly forward in time)
        # Partition data into n_folds slices
        total_rows = len(df)
        # Minimum window: 50% train, 15% val, 15% test
        fold_test_size = int(total_rows * 0.15)
        fold_val_size = int(total_rows * 0.10)
        
        for k in range(n_folds):
            # Rolling forward
            # Fold k: test slice is at end of rolling window
            test_end_idx = total_rows - (n_folds - 1 - k) * (fold_test_size // 2)
            test_start_idx = test_end_idx - fold_test_size
            val_start_idx = test_start_idx - fold_val_size
            train_start_idx = max(0, val_start_idx - int(total_rows * 0.50))
            
            if val_start_idx <= train_start_idx or test_start_idx <= val_start_idx or test_end_idx <= test_start_idx:
                continue
                
            tr = df.iloc[train_start_idx:val_start_idx].copy().reset_index(drop=True)
            va = df.iloc[val_start_idx:test_start_idx].copy().reset_index(drop=True)
            te = df.iloc[test_start_idx:test_end_idx].copy().reset_index(drop=True)
            
            if len(tr) < 20 or len(va) < 5 or len(te) < 5:
                continue
                
            folds.append(WalkForwardFold(
                fold_idx=k + 1,
                train_df=tr, val_df=va, test_df=te,
                train_start=str(tr['timestamp'].min()), train_end=str(tr['timestamp'].max()),
                val_start=str(va['timestamp'].min()), val_end=str(va['timestamp'].max()),
                test_start=str(te['timestamp'].min()), test_end=str(te['timestamp'].max()),
                train_rows=len(tr), val_rows=len(va), test_rows=len(te)
            ))
        return folds, None
    else:
        return [], f"INSUFFICIENT_DATA: Dataset span is {span_days:.2f} days, but requested walk-forward requires {required_days:.1f} continuous calendar days."
