# quantum/features.py
"""Feature extraction and deterministic normalization for quantum models.
Uses the existing add_features function to compute raw features, then
produces a fixed‑order vector with z‑score normalisation.
"""
import numpy as np
import pandas as pd
try:
    from features import add_features
except ImportError:
    from ..features import add_features

# List of feature columns that are available after add_features
FEATURE_COLUMNS = [
    "returns",
    "log_returns",
    "body_size",
    "upper_wick",
    "lower_wick",
    "range",
    "ema_9",
    "ema_21",
    "ema_50",
    "ema_200",
    "dist_ema_21",
    "dist_ema_200",
    "trend_slope_21",
    "rsi_14",
    "macd",
    "macd_signal",
    "macd_hist",
    "atr_14",
    "atr",
    "atr_pct",
    "bb_middle",
    "bb_std",
    "bb_upper",
    "bb_lower",
    "bb_width",
    "bb_pos",
    "supertrend",
    "st_upper",
    "st_lower",
    "vol_sma_20",
    "rel_volume",
    "delta_sma_20",
    "rel_vol_delta",
]

def extract_feature_vector(df: pd.DataFrame) -> np.ndarray:
    """Return a deterministic, normalised feature vector (1‑D numpy array).
    The function:
    1. Calls ``add_features`` to ensure all raw columns exist.
    2. Selects the predefined ``FEATURE_COLUMNS`` (ignores missing columns).
    3. Applies z‑score normalisation using the mean/std of the provided slice –
       this is deterministic because it depends only on the input data.
    4. Returns the vector for the *most recent* row (last candle) which is what
       the live model consumes.
    """
    df = add_features(df)
    # Ensure all expected columns exist; if not, fill with NaN
    missing = set(FEATURE_COLUMNS) - set(df.columns)
    for col in missing:
        df[col] = np.nan
    # Take the last row (latest candle)
    row = df[FEATURE_COLUMNS].iloc[-1].astype(float)
    # Deterministic z‑score normalisation (mean/std of this row is zero/one –
    # we instead normalise using the column‑wide statistics of the supplied df)
    means = df[FEATURE_COLUMNS].mean()
    stds = df[FEATURE_COLUMNS].std().replace(0, 1)  # avoid division by zero
    norm = (row - means) / stds
    # Fill any NaNs with zero (neutral value)
    norm = norm.fillna(0.0)
    return norm.values.astype(np.float32)
