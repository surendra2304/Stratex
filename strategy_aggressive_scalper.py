"""
strategy_aggressive_scalper.py — Multi-Timeframe Hyper-Aggressive Scalper (Futures)

ARCHITECTURE:
  - Timeframes: 1m, 5m, 15m, 30m, 1h, 4h
  - Logic: Fast EMA(9) crosses Slow EMA(21) on any active timeframe.
      * Long Entry:  EMA(9) crosses above EMA(21) -> BUY
      * Short Entry: EMA(9) crosses below EMA(21) -> SELL
  - Exits:
      * Stop Loss (SL):   0.5 × ATR(14)
      * Take Profit (TP): 1.0 × ATR(14) (Risk/Reward = 1:2.0)
  - Filters: None (Pure multi-timeframe price action crossover)
"""

from collections import namedtuple
import pandas as pd


class SignalResult(namedtuple("SignalResult", ["side", "sl", "tp", "strategy_type", "win_rate_prior", "rr_ratio"])):
    @property
    def confidence(self):
        return self.win_rate_prior


_STRATEGY_TYPE      = "RULE_BASED"
_OOS_WIN_RATE_PRIOR = 0.50
_RR_RATIO           = 2.0
_SL_ATR             = 0.5
_TP_ATR             = 1.0


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
    1m EMA(9)/EMA(21) crossover signal generator.
    """
    _NO_SIGNAL = SignalResult(None, None, None, _STRATEGY_TYPE, _OOS_WIN_RATE_PRIOR, _RR_RATIO)

    if df is None or len(df) < 25:
        return _NO_SIGNAL

    if 'ema_9' not in df.columns or 'ema_21' not in df.columns or 'atr' not in df.columns:
        df = add_features(df)

    last = df.iloc[-1]
    prev = df.iloc[-2]

    if pd.isna(last['ema_9']) or pd.isna(last['ema_21']) or pd.isna(last['atr']):
        return _NO_SIGNAL

    atr_val = last['atr']
    if atr_val <= 0:
        return _NO_SIGNAL

    close_p = last['close']
    cross_up = (last['ema_9'] > last['ema_21']) and (prev['ema_9'] <= prev['ema_21'])
    cross_dn = (last['ema_9'] < last['ema_21']) and (prev['ema_9'] >= prev['ema_21'])

    if cross_up:
        sl = close_p - (_SL_ATR * atr_val)
        tp = close_p + (_TP_ATR * atr_val)
        return SignalResult("BUY", sl, tp, _STRATEGY_TYPE, _OOS_WIN_RATE_PRIOR, _RR_RATIO)

    if cross_dn:
        sl = close_p + (_SL_ATR * atr_val)
        tp = close_p - (_TP_ATR * atr_val)
        return SignalResult("SELL", sl, tp, _STRATEGY_TYPE, _OOS_WIN_RATE_PRIOR, _RR_RATIO)

    return _NO_SIGNAL
