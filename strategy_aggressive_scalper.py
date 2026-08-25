"""
strategy_aggressive_scalper.py — Multi-Timeframe Hyper-Aggressive Scalper (Futures)

ARCHITECTURE:
  - Timeframes: 1m, 5m, 15m, 30m, 1h, 4h
  - Logic: Hyper-frequency green/red candle close on any active timeframe.
      * Long Entry:  Close > Open -> BUY
      * Short Entry: Close < Open -> SELL
  - Exits:
      * Stop Loss (SL):   1.5 × ATR(14) (Room to breathe against 1m noise)
      * Take Profit (TP): 0.75 × ATR(14) (Fast profit capture before reversal)
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
_RR_RATIO           = 0.5
_SL_ATR             = 1.5
_TP_ATR             = 0.75


def compute_atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """ATR(period) using Wilder's EWM smoothing."""
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
    - Candle closes GREEN (Close > Open) -> BUY (Long)
    - Candle closes RED (Close < Open)   -> SELL (Short)
    Exits: SL = 1.5 × ATR(14), TP = 0.75 × ATR(14)
    """
    _NO_SIGNAL = SignalResult(None, None, None, _STRATEGY_TYPE, _OOS_WIN_RATE_PRIOR, _RR_RATIO)

    if df is None or len(df) < 15:
        return _NO_SIGNAL

    if 'atr' not in df.columns:
        df = add_features(df)

    last = df.iloc[-1]

    atr_val = last.get('atr', compute_atr(df, 14).iloc[-1])
    if pd.isna(atr_val) or atr_val <= 0:
        atr_val = last['close'] * 0.01

    close_p = float(last['close'])
    open_p = float(last['open'])

    if close_p > open_p:
        sl = close_p - (_SL_ATR * atr_val)
        tp = close_p + (_TP_ATR * atr_val)
        return SignalResult("BUY", sl, tp, _STRATEGY_TYPE, _OOS_WIN_RATE_PRIOR, _RR_RATIO)

    if close_p < open_p:
        sl = close_p + (_SL_ATR * atr_val)
        tp = close_p - (_TP_ATR * atr_val)
        return SignalResult("SELL", sl, tp, _STRATEGY_TYPE, _OOS_WIN_RATE_PRIOR, _RR_RATIO)

    return _NO_SIGNAL
