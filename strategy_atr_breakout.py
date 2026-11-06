# strategy_atr_breakout.py - ATR breakout trend following
"""Daily ATR breakout with a long-term trend filter.

Entry: close breaks beyond prior close +/- 1.5 * ATR(14) while EMA50 > EMA200
       (mirrored for shorts). Stop 2.0 x ATR, safety TP 5.0 x ATR.
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
_OOS_WIN_RATE_PRIOR = 0.39
_RR_RATIO = 5.0

_EMA_FAST = 50
_EMA_SLOW = 200
_ATR_LEN = 14
_ATR_MULT_ENTRY = 1.5
_ATR_MULT_STOP = 2.0
_TP_ATR = 5.0
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
    """Return a BUY/SELL on an ATR breakout, else a HOLD signal."""
    if df is None or len(df) < _MIN_BARS:
        return _none()

    work = _drop_forming_bar(df)
    if len(work) < _MIN_BARS - 2:
        return _none()

    required = {"close", "high", "low"}
    if not required.issubset(work.columns):
        return _none()

    close = work["close"].astype(float)
    high = work["high"].astype(float)
    low = work["low"].astype(float)

    ema_fast = close.ewm(span=_EMA_FAST, adjust=False).mean()
    ema_slow = close.ewm(span=_EMA_SLOW, adjust=False).mean()

    prev_close = close.shift(1)
    tr = pd.concat(
        [high - low, (high - prev_close).abs(), (low - prev_close).abs()], axis=1
    ).max(axis=1)
    atr = tr.rolling(_ATR_LEN).mean()

    c = float(close.iloc[-1])
    pc = float(prev_close.iloc[-1])
    a = float(atr.iloc[-1])
    ef = float(ema_fast.iloc[-1])
    es = float(ema_slow.iloc[-1])

    if any(math.isnan(v) for v in (c, pc, a, ef, es)) or a <= 0 or c <= 0:
        return _none()

    sl_dist = _ATR_MULT_STOP * a
    tp_dist = _TP_ATR * a

    if c > (pc + _ATR_MULT_ENTRY * a) and ef > es:
        return SignalResult(
            "BUY", c - sl_dist, c + tp_dist,
            _STRATEGY_TYPE, _OOS_WIN_RATE_PRIOR, _RR_RATIO,
            sl_dist, tp_dist,
        )
    if c < (pc - _ATR_MULT_ENTRY * a) and ef < es:
        return SignalResult(
            "SELL", c + sl_dist, c - tp_dist,
            _STRATEGY_TYPE, _OOS_WIN_RATE_PRIOR, _RR_RATIO,
            sl_dist, tp_dist,
        )
    return _none()