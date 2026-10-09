"""
strategy_aggressive_scalper.py — Multi-Timeframe Hyper-Aggressive Scalper (Futures)

ARCHITECTURE:
  - Timeframes: 1m, 5m, 15m, 30m, 1h, 4h
  - Logic: Hyper-frequency green/red candle close on any active timeframe.
      * Long Entry:  Close > Open -> BUY
      * Short Entry: Close < Open -> SELL
  - Exits (Fixed Percentages):
      * Long:  SL = Entry × 0.995 (0.5% away), TP = Entry × 1.003 (0.3% away)
      * Short: SL = Entry × 1.005 (0.5% away), TP = Entry × 0.997 (0.3% away)
  - Filters: None (Pure multi-timeframe price action)
"""

import math
from collections import namedtuple

import pandas as pd


class SignalResult(namedtuple("SignalResult", ["side", "sl", "tp", "strategy_type", "win_rate_prior", "rr_ratio"])):
    @property
    def confidence(self):
        return self.win_rate_prior


_STRATEGY_TYPE      = "RULE_BASED"
_OOS_WIN_RATE_PRIOR = 0.50
_RR_RATIO           = 0.6  # 0.3% TP / 0.5% SL = 0.6
_SL_PCT             = 0.005  # 0.5% Stop Loss
_TP_PCT             = 0.003  # 0.3% Take Profit


def compute_atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """ATR(period) using Wilder's EWM smoothing (retained for backward compatibility)."""
    tr = pd.concat([
        df['high'] - df['low'],
        abs(df['high'] - df['close'].shift(1)),
        abs(df['low']  - df['close'].shift(1))
    ], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / period, adjust=False).mean()


def add_features(df: pd.DataFrame) -> pd.DataFrame:
    """Computes EMA(9), EMA(21), and ATR(14)."""
    df = df.copy()
    df['ema_9']  = df['close'].ewm(span=9,  adjust=False).mean()
    df['ema_21'] = df['close'].ewm(span=21, adjust=False).mean()
    df['atr']    = compute_atr(df, 14)
    return df


def get_signal(df: pd.DataFrame, **kwargs) -> SignalResult:
    """
    Hyper-frequency price action signal generator:
    - Candle closes GREEN (Close > Open) -> BUY (Long) with SL = Entry * (1 - sl_pct), TP = Entry * (1 + tp_pct)
    - Candle closes RED (Close < Open)   -> SELL (Short) with SL = Entry * (1 + sl_pct), TP = Entry * (1 - tp_pct)
    """
    _NO_SIGNAL = SignalResult(None, None, None, _STRATEGY_TYPE, _OOS_WIN_RATE_PRIOR, _RR_RATIO)

    if df is None or len(df) < 2:
        return _NO_SIGNAL

    try:
        from advisory_params import get_param
        sl_pct = float(get_param("aggressive_scalper", "sl_pct", _SL_PCT))
        tp_pct = float(get_param("aggressive_scalper", "tp_pct", _TP_PCT))
    except Exception:
        sl_pct = _SL_PCT
        tp_pct = _TP_PCT
    # An overlay value outside (0, 1) would put the stop on the wrong side of
    # (or at) the entry: no signal rather than an unprotected order.
    if not (0.0 < sl_pct < 1.0 and 0.0 < tp_pct < 1.0):
        return _NO_SIGNAL

    last = df.iloc[-1]
    try:
        close_p = float(last['close'])
        open_p = float(last['open'])
    except (KeyError, TypeError, ValueError):
        return _NO_SIGNAL
    if not (math.isfinite(close_p) and math.isfinite(open_p) and close_p > 0 and open_p > 0):
        return _NO_SIGNAL

    # No rounding here: round(x, 4) collapsed SL/TP of sub-0.0001 assets to 0
    # (or onto the entry). Tick-size rounding belongs to the execution layer.
    if close_p > open_p:
        sl = close_p * (1.0 - sl_pct)
        tp = close_p * (1.0 + tp_pct)
        return SignalResult("BUY", sl, tp, _STRATEGY_TYPE, _OOS_WIN_RATE_PRIOR, _RR_RATIO)

    if close_p < open_p:
        sl = close_p * (1.0 + sl_pct)
        tp = close_p * (1.0 - tp_pct)
        return SignalResult("SELL", sl, tp, _STRATEGY_TYPE, _OOS_WIN_RATE_PRIOR, _RR_RATIO)

    return _NO_SIGNAL
