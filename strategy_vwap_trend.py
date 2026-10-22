"""
strategy_vwap_trend.py — VWAP Trend Follow (Futures)

ARCHITECTURE:
  - Logic:
      * Long Entry:  Price crosses above VWAP and EMA(9) > EMA(21) -> BUY
      * Short Entry: Price crosses below VWAP and EMA(9) < EMA(21) -> SELL
  - Exits:
      * Stop Loss (SL):   0.5 × ATR(14)
      * Take Profit (TP): 1.5 × ATR(14) (Risk/Reward = 1:3.0)
"""

from collections import namedtuple

import pandas as pd


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


def compute_vwap(df: pd.DataFrame, window: int = 20) -> pd.Series:
    """Rolling VWAP over window bars (robust for continuous testnet kline streams)."""
    typical_price = (df['high'] + df['low'] + df['close']) / 3.0
    vol = df['volume']
    tp_vol = (typical_price * vol).rolling(window=window, min_periods=5).sum()
    sum_vol = vol.rolling(window=window, min_periods=5).sum()
    vwap = tp_vol / (sum_vol + 1e-12)
    # Fallback to EMA20 if volume is zero across the window
    return vwap.fillna(df['close'].ewm(span=20, adjust=False).mean())


def add_features(df: pd.DataFrame) -> pd.DataFrame:
    """Computes VWAP, EMA(9), EMA(21), and ATR(14)."""
    df = df.copy()
    df['ema_9']  = df['close'].ewm(span=9,  adjust=False).mean()
    df['ema_21'] = df['close'].ewm(span=21, adjust=False).mean()
    df['vwap']   = compute_vwap(df, 20)
    df['atr']    = compute_atr(df, 14)
    return df


def get_signal(df: pd.DataFrame, **kwargs) -> SignalResult:
    """
    VWAP Trend Follow signal generator.
    """
    _NO_SIGNAL = SignalResult(None, None, None, _STRATEGY_TYPE, _OOS_WIN_RATE_PRIOR, _RR_RATIO)

    if df is None or len(df) < 25:
        return _NO_SIGNAL

    if 'vwap' not in df.columns or 'ema_9' not in df.columns or 'ema_21' not in df.columns or 'atr' not in df.columns:
        df = add_features(df)

    last = df.iloc[-1]
    prev = df.iloc[-2]

    if pd.isna(last['vwap']) or pd.isna(last['ema_9']) or pd.isna(last['ema_21']) or pd.isna(last['atr']):
        return _NO_SIGNAL

    atr_val = last['atr']
    if atr_val <= 0:
        return _NO_SIGNAL

    close_p = last['close']
    prev_close = prev['close']
    last_vwap = last['vwap']
    prev_vwap = prev['vwap']
    ema9 = last['ema_9']
    ema21 = last['ema_21']

    # Cross above VWAP + EMA9 > EMA21
    cross_above_vwap = (prev_close <= prev_vwap) and (close_p > last_vwap)
    # Cross below VWAP + EMA9 < EMA21
    cross_below_vwap = (prev_close >= prev_vwap) and (close_p < last_vwap)

    if cross_above_vwap and (ema9 > ema21):
        sl = close_p - (_SL_ATR * atr_val)
        tp = close_p + (_TP_ATR * atr_val)
        return SignalResult("BUY", sl, tp, _STRATEGY_TYPE, _OOS_WIN_RATE_PRIOR, _RR_RATIO)

    if cross_below_vwap and (ema9 < ema21):
        sl = close_p + (_SL_ATR * atr_val)
        tp = close_p - (_TP_ATR * atr_val)
        return SignalResult("SELL", sl, tp, _STRATEGY_TYPE, _OOS_WIN_RATE_PRIOR, _RR_RATIO)

    return _NO_SIGNAL
