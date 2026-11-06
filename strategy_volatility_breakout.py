# strategy_volatility_breakout.py - ATR volatility expansion breakout
"""Volatility breakout: enter when ATR expands beyond recent average.
- BUY when close breaks above prior 10-day high with ATR expanding and trend up
- SELL when close breaks below prior 10-day low with ATR expanding and trend down
- Stop: 2.5 x ATR(14), safety TP: 6.0 x ATR(14)
- Exit: EMA20 cross
"""

from __future__ import annotations

import math
from collections import namedtuple

import pandas as pd


class SignalResult(namedtuple("SignalResult", ["side", "sl", "tp", "strategy_type", "win_rate_prior", "rr_ratio", "sl_dist", "tp_dist"], defaults=(None, None, None, None))):
    @property
    def confidence(self):
        return self.win_rate_prior


_STRATEGY_TYPE = "TREND_FOLLOWING"
_OOS_WIN_RATE_PRIOR = 0.38
_RR_RATIO = 6.0
_ATR_MULT = 2.5
_TP_ATR = 6.0


def _none():
    return SignalResult(None, None, None, _STRATEGY_TYPE, _OOS_WIN_RATE_PRIOR, _RR_RATIO, None, None)


def _drop_forming_bar(df):
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


def get_signal(df):
    if df is None or len(df) < 210:
        return _none()

    work = _drop_forming_bar(df)
    if len(work) < 205:
        return _none()

    required = {"close", "high", "low"}
    if not required.issubset(work.columns):
        return _none()

    close = work["close"].astype(float)
    high = work["high"].astype(float)
    low = work["low"].astype(float)

    ema_fast = close.ewm(span=50, adjust=False).mean()
    ema_slow = close.ewm(span=200, adjust=False).mean()

    prev_close = close.shift(1)
    tr = pd.concat([high - low, (high - prev_close).abs(), (low - prev_close).abs()], axis=1).max(axis=1)
    atr = tr.rolling(14).mean()
    atr_avg = atr.rolling(20).mean()

    prior_high_10 = high.rolling(10).max().shift(1)
    prior_low_10 = low.rolling(10).min().shift(1)

    c = float(close.iloc[-1])
    a = float(atr.iloc[-1])
    a_avg = float(atr_avg.iloc[-1])
    ef = float(ema_fast.iloc[-1])
    es = float(ema_slow.iloc[-1])
    ph = float(prior_high_10.iloc[-1])
    pl = float(prior_low_10.iloc[-1])

    if any(math.isnan(v) for v in (a, a_avg, ef, es, ph, pl)) or a <= 0 or c <= 0 or a_avg <= 0:
        return _none()

    # Volatility expansion: current ATR > 1.2x recent average
    vol_expanding = a > 1.2 * a_avg

    # Trend filter
    trend_up = ef > es
    trend_down = ef < es

    # BUY: close breaks above 10-day high with expanding volatility in uptrend
    if c > ph and vol_expanding and trend_up:
        return SignalResult("BUY", c - _ATR_MULT * a, c + _TP_ATR * a,
                            _STRATEGY_TYPE, _OOS_WIN_RATE_PRIOR, _RR_RATIO,
                            _ATR_MULT * a, _TP_ATR * a)

    # SELL: close breaks below 10-day low with expanding volatility in downtrend
    if c < pl and vol_expanding and trend_down:
        return SignalResult("SELL", c + _ATR_MULT * a, c - _TP_ATR * a,
                            _STRATEGY_TYPE, _OOS_WIN_RATE_PRIOR, _RR_RATIO,
                            _ATR_MULT * a, _TP_ATR * a)
    return _none()
