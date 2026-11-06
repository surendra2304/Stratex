# strategy_trend_continuation.py - Trend continuation (higher highs / lower lows)
"""Daily trend-continuation entries on higher-high / higher-low (and the
mirrored lower-low / lower-high) structure with a long-term trend filter.

Entry:
  BUY  when close > prior 5-day high AND low > prior 5-day low AND EMA50 > EMA200
  SELL when close < prior 5-day low  AND high < prior 5-day high AND EMA50 < EMA200
Stop : 2.0 x ATR(14), placed exchange-side
TP   : 4.0 x ATR(14) safety order (rare)
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
_OOS_WIN_RATE_PRIOR = 0.41
_RR_RATIO = 4.0

_LOOKBACK = 5
_EMA_FAST = 50
_EMA_SLOW = 200
_EMA_EXIT = 20
_ATR_LEN = 14
_ATR_MULT = 2.0
_TP_ATR = 4.0
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
    """Return a BUY/SELL continuation signal on completed bars, else HOLD."""
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

    ema_fast = close.ewm(span=_EMA_FAST, adjust=False).mean()
    ema_slow = close.ewm(span=_EMA_SLOW, adjust=False).mean()

    prev_close = close.shift(1)
    tr = pd.concat(
        [high - low, (high - prev_close).abs(), (low - prev_close).abs()], axis=1
    ).max(axis=1)
    atr = tr.rolling(_ATR_LEN).mean()

    prior_high = high.rolling(_LOOKBACK).max().shift(1)
    prior_low = low.rolling(_LOOKBACK).min().shift(1)

    c = float(close.iloc[-1])
    a = float(atr.iloc[-1])
    ph = float(prior_high.iloc[-1])
    pl = float(prior_low.iloc[-1])
    ef = float(ema_fast.iloc[-1])
    es = float(ema_slow.iloc[-1])
    last_high = float(high.iloc[-1])
    last_low = float(low.iloc[-1])

    if any(math.isnan(v) for v in (a, ph, pl, ef, es, last_high, last_low)) or a <= 0 or c <= 0:
        return _none()

    if c > ph and last_low > pl and ef > es:
        return SignalResult(
            "BUY", c - _ATR_MULT * a, c + _TP_ATR * a,
            _STRATEGY_TYPE, _OOS_WIN_RATE_PRIOR, _RR_RATIO,
            _ATR_MULT * a, _TP_ATR * a,
        )
    if c < pl and last_high < ph and ef < es:
        return SignalResult(
            "SELL", c + _ATR_MULT * a, c - _TP_ATR * a,
            _STRATEGY_TYPE, _OOS_WIN_RATE_PRIOR, _RR_RATIO,
            _ATR_MULT * a, _TP_ATR * a,
        )
    return _none()
