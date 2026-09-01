"""
strategy_bb_reversion.py — Bollinger Band Mean Reversion (Futures)

ARCHITECTURE:
  - Logic:
      * Long Entry:  Previous candle low/close pierced lower Bollinger Band (20, 2 StdDev), current close closes back inside -> BUY
      * Short Entry: Previous candle high/close pierced upper Bollinger Band (20, 2 StdDev), current close closes back inside -> SELL
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


def add_features(df: pd.DataFrame) -> pd.DataFrame:
    """Computes Bollinger Bands (20, 2) and ATR(14)."""
    df = df.copy()
    df['bb_mid'] = df['close'].rolling(window=20).mean()
    df['bb_std'] = df['close'].rolling(window=20).std()
    df['bb_upper'] = df['bb_mid'] + (2.0 * df['bb_std'])
    df['bb_lower'] = df['bb_mid'] - (2.0 * df['bb_std'])
    df['atr'] = compute_atr(df, 14)
    return df


def get_signal(df: pd.DataFrame, **kwargs) -> SignalResult:
    """
    Bollinger Band Mean Reversion signal generator.
    """
    _NO_SIGNAL = SignalResult(None, None, None, _STRATEGY_TYPE, _OOS_WIN_RATE_PRIOR, _RR_RATIO)

    if df is None or len(df) < 25:
        return _NO_SIGNAL

    if 'bb_upper' not in df.columns or 'bb_lower' not in df.columns or 'atr' not in df.columns:
        df = add_features(df)

    last = df.iloc[-1]
    prev = df.iloc[-2]

    if pd.isna(last['bb_upper']) or pd.isna(last['bb_lower']) or pd.isna(last['atr']):
        return _NO_SIGNAL

    atr_val = last['atr']
    if atr_val <= 0:
        return _NO_SIGNAL

    close_p = last['close']
    
    # Long: Pierced below lower band, now closes back above lower band
    pierce_lower = (prev['low'] < prev['bb_lower']) or (prev['close'] < prev['bb_lower'])
    reenter_long = (close_p > last['bb_lower']) and (close_p > prev['close'])
    
    # Short: Pierced above upper band, now closes back below upper band
    pierce_upper = (prev['high'] > prev['bb_upper']) or (prev['close'] > prev['bb_upper'])
    reenter_short = (close_p < last['bb_upper']) and (close_p < prev['close'])

    if pierce_lower and reenter_long:
        sl = close_p - (_SL_ATR * atr_val)
        tp = close_p + (_TP_ATR * atr_val)
        return SignalResult("BUY", sl, tp, _STRATEGY_TYPE, _OOS_WIN_RATE_PRIOR, _RR_RATIO)

    if pierce_upper and reenter_short:
        sl = close_p + (_SL_ATR * atr_val)
        tp = close_p - (_TP_ATR * atr_val)
        return SignalResult("SELL", sl, tp, _STRATEGY_TYPE, _OOS_WIN_RATE_PRIOR, _RR_RATIO)

    return _NO_SIGNAL
