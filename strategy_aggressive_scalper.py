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
    - Candle closes GREEN (Close > Open) -> BUY (Long) with SL = Entry * 0.995, TP = Entry * 1.003
    - Candle closes RED (Close < Open)   -> SELL (Short) with SL = Entry * 1.005, TP = Entry * 0.997
    """
    _NO_SIGNAL = SignalResult(None, None, None, _STRATEGY_TYPE, _OOS_WIN_RATE_PRIOR, _RR_RATIO)

    if df is None or len(df) < 2:
        return _NO_SIGNAL

    last = df.iloc[-1]
    close_p = float(last['close'])
    open_p = float(last['open'])

    if close_p > open_p:
        sl = round(close_p * (1.0 - _SL_PCT), 4)  # Entry * 0.995
        tp = round(close_p * (1.0 + _TP_PCT), 4)  # Entry * 1.003
        return SignalResult("BUY", sl, tp, _STRATEGY_TYPE, _OOS_WIN_RATE_PRIOR, _RR_RATIO)

    if close_p < open_p:
        sl = round(close_p * (1.0 + _SL_PCT), 4)  # Entry * 1.005
        tp = round(close_p * (1.0 - _TP_PCT), 4)  # Entry * 0.997
        return SignalResult("SELL", sl, tp, _STRATEGY_TYPE, _OOS_WIN_RATE_PRIOR, _RR_RATIO)

    return _NO_SIGNAL
