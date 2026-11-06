# strategy_volume_breakout.py - Volume-Weighted Breakout
"""Daily (1d) volume-weighted breakout with a long-term trend filter.

Entry requires a volume spike (> 1.5x the 20-day average volume) confirming a
breakout of the prior 10-day high/low, with the EMA50/EMA200 trend aligned.
Stop is 2.5 x ATR(14) placed exchange-side; a 5.0 x ATR(14) take-profit acts
as a rare safety order. The real exit is an EMA20 cross.

The daemon feeds only completed daily bars, so a signal cannot appear and
disappear intraday.
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

_VOL_LOOKBACK = 20
_BREAKOUT = 10
_EMA_TREND_FAST = 50
_EMA_TREND_SLOW = 200
_EMA_EXIT = 20
_ATR_LEN = 14
_ATR_MULT = 2.5
_TP_ATR = 5.0
_VOL_MULT = 1.5
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
    """Return a BUY/SELL on a completed volume-confirmed breakout, else HOLD."""
    if df is None or len(df) < _MIN_BARS:
        return _none()

    work = _drop_forming_bar(df)
    if len(work) < _MIN_BARS - 2:
        return _none()

    required = {"close", "high", "low", "volume"}
    if not required.issubset(work.columns):
        return _none()

    close = work["close"].astype(float)
    high = work["high"].astype(float)
    low = work["low"].astype(float)
    volume = work["volume"].astype(float)

    ema_trend_fast = close.ewm(span=_EMA_TREND_FAST, adjust=False).mean()
    ema_trend_slow = close.ewm(span=_EMA_TREND_SLOW, adjust=False).mean()

    prev_close = close.shift(1)
    tr = pd.concat(
        [high - low, (high - prev_close).abs(), (low - prev_close).abs()], axis=1
    ).max(axis=1)
    atr = tr.rolling(_ATR_LEN).mean()

    prior_high = high.rolling(_BREAKOUT).max().shift(1)
    prior_low = low.rolling(_BREAKOUT).min().shift(1)

    vol_avg = volume.rolling(_VOL_LOOKBACK).mean()

    c = float(close.iloc[-1])
    a = float(atr.iloc[-1])
    ph = float(prior_high.iloc[-1])
    pl = float(prior_low.iloc[-1])
    ef = float(ema_trend_fast.iloc[-1])
    es = float(ema_trend_slow.iloc[-1])
    v = float(volume.iloc[-1])
    va = float(vol_avg.iloc[-1])

    if any(math.isnan(x) for x in (a, ph, pl, ef, es, v, va)) or a <= 0 or c <= 0 or va <= 0:
        return _none()

    vol_spike = v > _VOL_MULT * va

    if vol_spike and c > ph and ef > es:
        sl_dist = _ATR_MULT * a
        tp_dist = _TP_ATR * a
        return SignalResult(
            "BUY", c - sl_dist, c + tp_dist,
            _STRATEGY_TYPE, _OOS_WIN_RATE_PRIOR, _RR_RATIO,
            sl_dist, tp_dist,
        )
    if vol_spike and c < pl and ef < es:
        sl_dist = _ATR_MULT * a
        tp_dist = _TP_ATR * a
        return SignalResult(
            "SELL", c + sl_dist, c - tp_dist,
            _STRATEGY_TYPE, _OOS_WIN_RATE_PRIOR, _RR_RATIO,
            sl_dist, tp_dist,
        )
    return _none()