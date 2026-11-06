# strategy_ema_crossover.py - EMA crossover trend following
"""EMA (12/26) crossover trend following with a long-term EMA50/EMA200 trend filter.

- BUY when EMA(12) crosses above EMA(26) while EMA50 > EMA200
- SELL when EMA(12) crosses below EMA(26) while EMA50 < EMA200
- Stop: 2.0 x ATR(14), safety TP: 5.0 x ATR(14)
- Exit: EMA20 cross (same proven exit rule)
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
_OOS_WIN_RATE_PRIOR = 0.38
_RR_RATIO = 5.0

_EMA_FAST = 12
_EMA_SLOW = 26
_EMA_TREND_FAST = 50
_EMA_TREND_SLOW = 200
_EMA_EXIT = 20
_ATR_LEN = 14
_ATR_MULT = 2.0
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


def add_features(df: pd.DataFrame) -> pd.DataFrame:
    """Compute EMA(12), EMA(26), EMA(50), EMA(200) and ATR(14)."""
    df = df.copy()
    close = df["close"].astype(float)
    df["ema_12"] = close.ewm(span=_EMA_FAST, adjust=False).mean()
    df["ema_26"] = close.ewm(span=_EMA_SLOW, adjust=False).mean()
    df["ema_20"] = close.ewm(span=_EMA_EXIT, adjust=False).mean()
    df["ema_50"] = close.ewm(span=_EMA_TREND_FAST, adjust=False).mean()
    df["ema_200"] = close.ewm(span=_EMA_TREND_SLOW, adjust=False).mean()
    high = df["high"].astype(float)
    low = df["low"].astype(float)
    prev_close = close.shift(1)
    tr = pd.concat(
        [high - low, (high - prev_close).abs(), (low - prev_close).abs()], axis=1
    ).max(axis=1)
    df["atr"] = tr.rolling(_ATR_LEN).mean()
    return df


def get_signal(df: pd.DataFrame):
    if df is None or len(df) < _MIN_BARS:
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

    if {"ema_12", "ema_26", "ema_50", "ema_200", "atr"}.issubset(work.columns):
        ema12 = work["ema_12"].astype(float)
        ema26 = work["ema_26"].astype(float)
        ema50 = work["ema_50"].astype(float)
        ema200 = work["ema_200"].astype(float)
        atr = work["atr"].astype(float)
    else:
        ema12 = close.ewm(span=_EMA_FAST, adjust=False).mean()
        ema26 = close.ewm(span=_EMA_SLOW, adjust=False).mean()
        ema50 = close.ewm(span=_EMA_TREND_FAST, adjust=False).mean()
        ema200 = close.ewm(span=_EMA_TREND_SLOW, adjust=False).mean()
        prev_close = close.shift(1)
        tr = pd.concat(
            [high - low, (high - prev_close).abs(), (low - prev_close).abs()], axis=1
        ).max(axis=1)
        atr = tr.rolling(_ATR_LEN).mean()

    c = float(close.iloc[-1])
    a = float(atr.iloc[-1])
    e12 = float(ema12.iloc[-1])
    e12p = float(ema12.iloc[-2])
    e26 = float(ema26.iloc[-1])
    e26p = float(ema26.iloc[-2])
    e50 = float(ema50.iloc[-1])
    e200 = float(ema200.iloc[-1])

    if any(math.isnan(v) for v in (a, e12, e12p, e26, e26p, e50, e200)) or a <= 0 or c <= 0:
        return _none()

    cross_up = e12p <= e26p and e12 > e26
    cross_down = e12p >= e26p and e12 < e26

    if cross_up and e50 > e200:
        return SignalResult(
            "BUY", c - _ATR_MULT * a, c + _TP_ATR * a,
            _STRATEGY_TYPE, _OOS_WIN_RATE_PRIOR, _RR_RATIO,
            _ATR_MULT * a, _TP_ATR * a,
        )
    if cross_down and e50 < e200:
        return SignalResult(
            "SELL", c + _ATR_MULT * a, c - _TP_ATR * a,
            _STRATEGY_TYPE, _OOS_WIN_RATE_PRIOR, _RR_RATIO,
            _ATR_MULT * a, _TP_ATR * a,
        )
    return _none()