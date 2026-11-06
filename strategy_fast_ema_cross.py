#!/usr/bin/env python3
"""strategy_fast_ema_cross.py - Fast EMA crossover for scalping"""
from __future__ import annotations
import math
from collections import namedtuple
import pandas as pd


class SignalResult(namedtuple("SignalResult", ["side", "sl", "tp", "strategy_type", "win_rate_prior", "rr_ratio", "sl_dist", "tp_dist"], defaults=(None, None))):
    @property
    def confidence(self):
        return self.win_rate_prior


_STRATEGY_TYPE = "SCALPING"
_OOS_WIN_RATE_PRIOR = 0.55
_RR_RATIO = 2.0
_ATR_MULT = 1.0
_TP_ATR = 2.0


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
    if df is None or len(df) < 30:
        return _none()
    work = _drop_forming_bar(df)
    if len(work) < 25:
        return _none()
    required = {"close", "high", "low"}
    if not required.issubset(work.columns):
        return _none()
    close = work["close"].astype(float)
    high = work["high"].astype(float)
    low = work["low"].astype(float)

    ema9 = close.ewm(span=9, adjust=False).mean()
    ema21 = close.ewm(span=21, adjust=False).mean()
    prev_close = close.shift(1)
    tr = pd.concat([high - low, (high - prev_close).abs(), (low - prev_close).abs()], axis=1).max(axis=1)
    atr = tr.rolling(14).mean()

    c = float(close.iloc[-1])
    c_prev = float(close.iloc[-2])
    e9_prev = float(ema9.iloc[-2])
    e21_prev = float(ema21.iloc[-2])
    e9_curr = float(ema9.iloc[-1])
    e21_curr = float(ema21.iloc[-1])
    a = float(atr.iloc[-1])

    if any(math.isnan(v) for v in (a, e9_prev, e21_prev, e9_curr, e21_curr)) or a <= 0 or c <= 0:
        return _none()

    # Fast EMA cross: EMA9 crosses above EMA21 = BUY, below = SELL
    if e9_prev <= e21_prev and e9_curr > e21_curr:
        return SignalResult("BUY", c - _ATR_MULT * a, c + _TP_ATR * a,
                            _STRATEGY_TYPE, _OOS_WIN_RATE_PRIOR, _RR_RATIO,
                            _ATR_MULT * a, _TP_ATR * a)
    if e9_prev >= e21_prev and e9_curr < e21_curr:
        return SignalResult("SELL", c + _ATR_MULT * a, c - _TP_ATR * a,
                            _STRATEGY_TYPE, _OOS_WIN_RATE_PRIOR, _RR_RATIO,
                            _ATR_MULT * a, _TP_ATR * a)
    return _none()
