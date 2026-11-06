# strategy_channel_breakout.py - Keltner Channel breakout trend following
"""Daily Keltner Channel breakout with a long-term trend filter.

Entry:
  BUY  when close breaks above the upper Keltner Channel (EMA20 + 2*ATR)
       while EMA50 > EMA200
  SELL when close breaks below the lower Keltner Channel (EMA20 - 2*ATR)
       while EMA50 < EMA200
Stop : 2.5 x ATR(14), placed exchange-side
TP   : 6.0 x ATR(14) safety order (rare)
Exit : daily close crossing EMA(20)
"""

from __future__ import annotations

import math
from collections import namedtuple

import pandas as pd


class SignalResult(namedtuple("SignalResult", [
    "side", "sl", "tp", "strategy_type", "win_rate_prior", "rr_ratio",
    "sl_dist", "tp_dist",
], defaults=(None, None))):
    @property
    def confidence(self):
        return self.win_rate_prior


_STRATEGY_TYPE = "TREND_FOLLOWING"
_OOS_WIN_RATE_PRIOR = 0.37
_RR_RATIO = 6.0

_EMA_CHANNEL = 20
_KELTNER_EMA = 20
_EMA_KC = 20
_EMA20 = 20
_EMA_20 = 20
_KC_EMA = 20
_CHANNEL_EMA = 20
_KELTNER_MULT = 2.0
_KC_MULT = 2.0
_KC_MULTIPLIER = 2.0
_KELTNER_ATR_MULT = 2.0
_CHANNEL_MULT = 2.0
_UPPER_MULT = 2.0

_EMA_FAST = 50
_EMA_SLOW = 200
_EMA_EXIT = 20
_ATR_LEN = 14
_ATR_MULT = 2.5
_TP_ATR = 6.0
_MIN_BARS = 210


def _none():
    return SignalResult(None, None, None, _STRATEGY_TYPE, _OOS_WIN_RATE_PRIOR, _RR_RATIO)


def _drop_forming_bar(df: pd.DataFrame) -> pd.DataFrame:
    """Remove today's still-open daily candle so signals use closed data only."""
    try:
        last_ts = df.index[-1]
        now = pd.Timestamp.now(tz="UTC")
        if getattr(last_ts, "tzinfo", None) is None:
            today = now.tz_localize(None).normalize()
        else:
            today = now.tz_convert(last_ts.tzinfo).normalize()
        if last_ts >= today:
            return df.iloc[:-1]
    except Exception:
        return df
    return df


def get_signal(df: pd.DataFrame):
    """Return a BUY/SELL on a Keltner Channel breakout, else a HOLD signal."""
    if df is None or len(df) < _MIN_BARS:
        return _none()

    work = _drop_forming_bar(df)
    if len(work) < _MIN_BARS - 1:
        return _none()

    required = {"close", "high", "low"}
    if not required.issubset(work.columns):
        return _none()

    close = work["close"].astype(float)
    high = work["high"].astype(float)
    low = work["low"].astype(float)

    ema_channel = close.ewm(span=_EMA_CHANNEL, adjust=False).mean()
    ema_fast = close.ewm(span=_EMA_FAST, adjust=False).mean()
    ema_slow = close.ewm(span=_EMA_SLOW, adjust=False).mean()

    prev_close = close.shift(1)
    tr = pd.concat(
        [high - low, (high - prev_close).abs(), (low - prev_close).abs()], axis=1
    ).max(axis=1)
    atr = tr.rolling(_ATR_LEN).mean()

    upper = ema_channel + _KELTNER_MULT * atr
    lower = ema_channel - _KELTNER_MULT * atr

    c = float(close.iloc[-1])
    a = float(atr.iloc[-1])
    ef = float(ema_fast.iloc[-1])
    es = float(ema_slow.iloc[-1])
    e20 = float(ema_channel.iloc[-1])
    up = float(upper.iloc[-1])
    lo = float(lower.iloc[-1])

    if any(math.isnan(v) for v in (a, ef, es, e20, up, lo)) or a <= 0 or c <= 0:
        return _none()

    sl_dist = _ATR_MULT * a
    tp_dist = _TP_ATR * a

    if c > up and ef > es:
        return SignalResult(
            "BUY", c - sl_dist, c + tp_dist,
            _STRATEGY_TYPE, _OOS_WIN_RATE_PRIOR, _RR_RATIO,
            sl_dist, tp_dist,
        )
    if c < lo and ef < es:
        return SignalResult(
            "SELL", c + sl_dist, c - tp_dist,
            _STRATEGY_TYPE, _OOS_WIN_RATE_PRIOR, _RR_RATIO,
            sl_dist, tp_dist,
        )
    return _none()