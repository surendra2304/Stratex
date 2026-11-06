# strategy_higher_highs.py - Higher Highs / Lower Lows trend following
"""Daily (1d) higher-highs / lower-lows swing structure with a long-term trend filter.

Entry when the last 3 bars print consecutively higher highs AND higher lows
(BUY) or lower highs AND lower lows (SELL), confirmed by EMA50 vs EMA200.
Stop is 1.5 x ATR(14); a 3.0 x ATR(14) take-profit acts as a safety order.
Real exit is the EMA20 cross.
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
_OOS_WIN_RATE_PRIOR = 0.43
_RR_RATIO = 3.0

_EMA_FAST = 50
_EMA_SLOW = 200
_ATR_LEN = 14
_ATR_MULT = 1.5
_TP_ATR = 3.0
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
    """Return a BUY/SELL on a 3-bar higher-highs/higher-lows structure, else HOLD."""
    if df is None or len(df) < _MIN_BARS:
        return _none()

    work = _drop_forming_bar(df)
    if len(work) < _MIN_BARS:
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

    higher_highs = bool(high.iloc[-1] > high.iloc[-2] > high.iloc[-3])
    higher_lows = bool(low.iloc[-1] > low.iloc[-2] > low.iloc[-3])
    lower_highs = bool(high.iloc[-1] < high.iloc[-2] < high.iloc[-3])
    lower_lows = bool(low.iloc[-1] < low.iloc[-2] < low.iloc[-3])

    c = float(close.iloc[-1])
    a = float(atr.iloc[-1])
    ef = float(ema_fast.iloc[-1])
    es = float(ema_slow.iloc[-1])

    if any(math.isnan(v) for v in (a, ef, es)) or a <= 0 or c <= 0:
        return _none()

    if higher_highs and higher_lows and ef > es:
        return SignalResult(
            "BUY", c - _ATR_MULT * a, c + _TP_ATR * a,
            _STRATEGY_TYPE, _OOS_WIN_RATE_PRIOR, _RR_RATIO,
            _ATR_MULT * a, _TP_ATR * a,
        )
    if lower_highs and lower_lows and ef < es:
        return SignalResult(
            "SELL", c + _ATR_MULT * a, c - _TP_ATR * a,
            _STRATEGY_TYPE, _OOS_WIN_RATE_PRIOR, _RR_RATIO,
            _ATR_MULT * a, _TP_ATR * a,
        )
    return _none()