# quantum/validation/data.py
"""Historical dataset loading, inspection, and verification."""

import os
import glob
import pandas as pd
import numpy as np
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

@dataclass
class DatasetAuditResult:
    symbol: str
    timeframe: str
    file_path: str
    rows: int
    start_time: str
    end_time: str
    span_days: float
    missing_data_intervals: int
    duplicate_timestamps: int
    is_sufficient_for_90d_wf: bool
    status: str

def inspect_dataset_file(file_path: str, symbol: str, timeframe: str) -> DatasetAuditResult:
    """Inspects a CSV or Parquet dataset for rows, timestamp gaps, and duplicates."""
    if not os.path.exists(file_path):
        return DatasetAuditResult(
            symbol=symbol,
            timeframe=timeframe,
            file_path=file_path,
            rows=0,
            start_time="N/A",
            end_time="N/A",
            span_days=0.0,
            missing_data_intervals=0,
            duplicate_timestamps=0,
            is_sufficient_for_90d_wf=False,
            status="MISSING_FILE"
        )
    
    if file_path.endswith('.parquet'):
        df = pd.read_parquet(file_path)
    else:
        df = pd.read_csv(file_path)
        
    if df.empty or 'timestamp' not in df.columns:
        return DatasetAuditResult(
            symbol=symbol,
            timeframe=timeframe,
            file_path=file_path,
            rows=len(df),
            start_time="N/A",
            end_time="N/A",
            span_days=0.0,
            missing_data_intervals=0,
            duplicate_timestamps=0,
            is_sufficient_for_90d_wf=False,
            status="EMPTY_OR_INVALID"
        )
        
    df['timestamp'] = pd.to_datetime(df['timestamp'])
    df = df.sort_values('timestamp').reset_index(drop=True)
    
    start_ts = df['timestamp'].min()
    end_ts = df['timestamp'].max()
    span_days = (end_ts - start_ts).total_seconds() / 86400.0
    duplicates = int(df['timestamp'].duplicated().sum())
    
    # Check for gaps
    diffs = df['timestamp'].diff()
    median_diff = diffs.median()
    gaps = int((diffs > (median_diff * 3)).sum()) if pd.notnull(median_diff) else 0
    
    # 5 folds of 60d train + 15d val + 15d test requires >= 90 days of continuous data
    is_sufficient = span_days >= 90.0
    status = "OK_SUFFICIENT" if is_sufficient else f"INSUFFICIENT_SPAN_{span_days:.1f}D_OF_90D_REQ"
    
    return DatasetAuditResult(
        symbol=symbol,
        timeframe=timeframe,
        file_path=file_path,
        rows=len(df),
        start_time=str(start_ts),
        end_time=str(end_ts),
        span_days=round(span_days, 2),
        missing_data_intervals=gaps,
        duplicate_timestamps=duplicates,
        is_sufficient_for_90d_wf=is_sufficient,
        status=status
    )

def audit_all_datasets(data_dir: str = "data_cache") -> List[DatasetAuditResult]:
    """Audits all available cache files in the workspace."""
    results = []
    patterns = [os.path.join(data_dir, "*_15m.csv"), os.path.join(data_dir, "*_1m_90d.parquet"), os.path.join(data_dir, "*_1h.csv")]
    files = []
    for pat in patterns:
        files.extend(glob.glob(pat))
    files = sorted(list(set(files)))
    
    for f in files:
        base = os.path.basename(f)
        parts = base.split('_')
        symbol = parts[0]
        tf = parts[1].replace('.csv', '').replace('.parquet', '')
        res = inspect_dataset_file(f, symbol, tf)
        results.append(res)
    return results

def load_benchmark_data(symbol: str = "BTCUSDT", preferred_tf: str = "15m", data_dir: str = "data_cache") -> Tuple[pd.DataFrame, DatasetAuditResult]:
    """Loads and standardizes historical candle data for benchmarking."""
    # Priority: 1m_90d.parquet resampled or direct 15m.csv
    parquet_path = os.path.join(data_dir, f"{symbol}_1m_90d.parquet")
    csv_path = os.path.join(data_dir, f"{symbol}_{preferred_tf}.csv")
    
    if os.path.exists(parquet_path):
        df_raw = pd.read_parquet(parquet_path)
        tf_used = "1m"
        path_used = parquet_path
    elif os.path.exists(csv_path):
        df_raw = pd.read_csv(csv_path)
        tf_used = preferred_tf
        path_used = csv_path
    else:
        # Fallback to any matching CSV
        matches = glob.glob(os.path.join(data_dir, f"{symbol}*.csv"))
        if matches:
            path_used = matches[0]
            df_raw = pd.read_csv(path_used)
            tf_used = "custom"
        else:
            return pd.DataFrame(), DatasetAuditResult(
                symbol=symbol, timeframe=preferred_tf, file_path="NONE",
                rows=0, start_time="N/A", end_time="N/A", span_days=0.0,
                missing_data_intervals=0, duplicate_timestamps=0,
                is_sufficient_for_90d_wf=False, status="NO_DATA_FOUND"
            )
            
    audit = inspect_dataset_file(path_used, symbol, tf_used)
    
    df_raw['timestamp'] = pd.to_datetime(df_raw['timestamp'])
    df_raw = df_raw.sort_values('timestamp').drop_duplicates('timestamp').reset_index(drop=True)
    
    # Ensure numeric OHLCV
    for c in ['open', 'high', 'low', 'close', 'volume']:
        if c in df_raw.columns:
            df_raw[c] = pd.to_numeric(df_raw[c], errors='coerce')
    df_raw.dropna(subset=['open', 'high', 'low', 'close', 'volume'], inplace=True)
    
    return df_raw, audit

def validate_dataset(df: pd.DataFrame) -> bool:
    """Rigorous chronological and structural integrity validation."""
    if df is None or df.empty or len(df) < 50:
        return False
    core_cols = ['open', 'high', 'low', 'close', 'volume', 'timestamp']
    for c in core_cols:
        if c not in df.columns:
            return False
    if df[core_cols].isnull().any().any():
        return False
    if not df['timestamp'].is_monotonic_increasing:
        return False
    if df['timestamp'].duplicated().any():
        return False
    invalid_ohlc = df[(df['high'] < df['low']) | (df['open'] > df['high']) | (df['open'] < df['low']) | (df['close'] > df['high']) | (df['close'] < df['low'])]
    if not invalid_ohlc.empty:
        return False
    return True
