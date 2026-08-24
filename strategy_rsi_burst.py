"""
strategy_rsi_burst.py — RSI Momentum Burst (Futures)

ARCHITECTURE:
  - Logic:
      * Long Entry:  RSI(14) was < 30 (oversold) on previous bar and crosses back >= 30 on current bar -> BUY
      * Short Entry: RSI(14) was > 70 (overbought) on previous bar and crosses back <= 70 on current bar -> SELL
  - Exits:
      * Stop Loss (SL):   0.5 × ATR(14)
      * Take Profit (TP): 1.5 × ATR(14) (Risk/Reward = 1:3.0)
"""

from collections import namedtuple
import pandas as pd
import numpy as np


class SignalResult(namedtuple("SignalResult", ["side", "sl", "tp", "strategy_type", "win_rate_prior", "rr_ratio"])):
    @property
    def confidence(self):
        return self.win_rate_prior


_STRATEGY_TYPE      = "RULE_BASED"
_OOS_WIN_RATE_PRIOR = 0.50
_RR_RATIO           = 3.0
_SL_ATR             = 0.5
_TP_ATR             = 1.5


def compute_atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """ATR(period) using Wilder's EWM smoothing."""
    tr = pd.concat([
        df['high'] - df['low'],
        abs(df['high'] - df['close'].shift(1)),
        abs(df['low']  - df['close'].shift(1))
    ], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / period, adjust=False).mean()


def compute_rsi(close: pd.Series, period: int = 14) -> pd.Series:
    """RSI(14) using Wilder's EWM smoothing."""
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / period, adjust=False).mean()
    rs = avg_gain / (avg_loss + 1e-12)
    rsi = 100.0 - (100.0 / (1.0 + rs))
    return rsi


def add_features(df: pd.DataFrame) -> pd.DataFrame:
    """Computes RSI(14) and ATR(14)."""
    df = df.copy()
    df['rsi_14'] = compute_rsi(df['close'], 14)
    df['atr'] = compute_atr(df, 14)
    return df


def get_signal(df: pd.DataFrame, **kwargs) -> SignalResult:
    """
    RSI Momentum Burst signal generator.
    """
    _NO_SIGNAL = SignalResult(None, None, None, _STRATEGY_TYPE, _OOS_WIN_RATE_PRIOR, _RR_RATIO)

    if df is None or len(df) < 25:
        return _NO_SIGNAL

    if 'rsi_14' not in df.columns or 'atr' not in df.columns:
        df = add_features(df)

    last = df.iloc[-1]
    prev = df.iloc[-2]

    if pd.isna(last['rsi_14']) or pd.isna(prev['rsi_14']) or pd.isna(last['atr']):
        return _NO_SIGNAL

    atr_val = last['atr']
    if atr_val <= 0:
        return _NO_SIGNAL

    close_p = last['close']
    rsi_last = last['rsi_14']
    rsi_prev = prev['rsi_14']

    # Cross back above 30 from oversold
    cross_above_30 = (rsi_prev < 30.0) and (rsi_last >= 30.0)
    # Cross back below 70 from overbought
    cross_below_70 = (rsi_prev > 70.0) and (rsi_last <= 70.0)

    if cross_above_30:
        sl = close_p - (_SL_ATR * atr_val)
        tp = close_p + (_TP_ATR * atr_val)
        return SignalResult("BUY", sl, tp, _STRATEGY_TYPE, _OOS_WIN_RATE_PRIOR, _RR_RATIO)

    if cross_below_70:
        sl = close_p + (_SL_ATR * atr_val)
        tp = close_p - (_TP_ATR * atr_val)
        return SignalResult("SELL", sl, tp, _STRATEGY_TYPE, _OOS_WIN_RATE_PRIOR, _RR_RATIO)

    return _NO_SIGNAL
