# strategy_breakout_retest.py - Breakout retest trend following
"""Breakout retest: enter on a pullback to a freshly broken 20-day level.

- BUY when price breaks above the prior 20-day high, pulls back to within
  0.5 ATR of the broken level, and trend is up (EMA50 > EMA200)
- SELL when price breaks below the prior 20-day low, rallies back to within
  0.5 ATR of the broken level, and trend is down (EMA50 < EMA200)
- Stop: 2.0 x ATR(14), safety TP: 4.0 x ATR(14)
- Exit: EMA20 cross
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
_OOS_WIN_RATE_PRIOR = 0.40
_RR_RATIO = 4.0

_DONCHIAN = 20
_EMA_FAST = 50
_EMA_SLOW = 200
_ATR_LEN = 14
_ATR_MULT = 2.0
_TP_ATR = 4.0
_RETEST_ATR = 0.5


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
    """Return a BUY/SELL on a completed breakout retest, else a HOLD signal."""
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

    ema_fast = close.ewm(span=_EMA_FAST, adjust=False).mean()
    ema_slow = close.ewm(span=_EMA_SLOW, adjust=False).mean()

    prev_close = close.shift(1)
    tr = pd.concat(
        [high - low, (high - prev_close).abs(), (low - prev_close).abs()], axis=1
    ).max(axis=1)
    atr = tr.rolling(_ATR_LEN).mean()

    prior_high = high.rolling(_DONCHIAN).max().shift(1)
    prior_low = low.rolling(_DONCHIAN).min().shift(1)

    c = float(close.iloc[-1])
    a = float(atr.iloc[-1])
    ph = float(prior_high.iloc[-1])
    pl = float(prior_low.iloc[-1])
    ef = float(ema_fast.iloc[-1])
    es = float(ema_slow.iloc[-1])

    if any(math.isnan(v) for v in (a, ph, pl, ef, es)) or a <= 0 or c <= 0:
        return _none()

    # BUY: broke above prior 20-day high, pulled back within 0.5 ATR, trend up
    if c > ph and (c - ph) <= _RETEST_ATR * a and ef > es:
        return SignalResult(
            "BUY", c - _ATR_MULT * a, c + _TP_ATR * a,
            _STRATEGY_TYPE, _OOS_WIN_RATE_PRIOR, _RR_RATIO,
            _ATR_MULT * a, _TP_ATR * a,
        )
    # SELL: broke below prior 20-day low, rallied back within 0.5 ATR, trend down
    if c < pl and (pl - c) <= _RETEST_ATR * a and ef < es:
        return SignalResult(
            "SELL", c + _ATR_MULT * a, c - _TP_ATR * a,
            _STRATEGY_TYPE, _OOS_WIN_RATE_PRIOR, _RR_RATIO,
            _ATR_MULT * a, _TP_ATR * a,
        )
    return _none()