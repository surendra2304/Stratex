# strategy_trend_pullback.py - Buy dips in established uptrends
"""Trend pullback: enter on a pullback to EMA support in an established trend.
- BUY when price pulls back to EMA20 in an uptrend (EMA50 > EMA200) with RSI < 45
- SELL when price rallies to EMA20 in a downtrend (EMA50 < EMA200) with RSI > 55
- Stop: 2.0 x ATR(14), safety TP: 4.0 x ATR(14)
- Exit: EMA20 cross (same proven exit rule)
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
_OOS_WIN_RATE_PRIOR = 0.42
_RR_RATIO = 4.0
_ATR_MULT = 2.0
_TP_ATR = 4.0


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
    ema20 = close.ewm(span=20, adjust=False).mean()

    # RSI 14
    delta = close.diff()
    gain = delta.clip(lower=0).rolling(14).mean()
    loss = (-delta.clip(upper=0)).rolling(14).mean()
    rs = gain / loss.replace(0, 1e-10)
    rsi = 100 - (100 / (1 + rs))

    prev_close = close.shift(1)
    tr = pd.concat([high - low, (high - prev_close).abs(), (low - prev_close).abs()], axis=1).max(axis=1)
    atr = tr.rolling(14).mean()

    c = float(close.iloc[-1])
    a = float(atr.iloc[-1])
    ef = float(ema_fast.iloc[-1])
    es = float(ema_slow.iloc[-1])
    e20 = float(ema20.iloc[-1])
    rsi_val = float(rsi.iloc[-1])

    if any(math.isnan(v) for v in (a, ef, es, e20, rsi_val)) or a <= 0 or c <= 0:
        return _none()

    # Pullback distance: close within 1.5 ATR of EMA20
    pullback_dist = abs(c - e20) / a

    # BUY: uptrend, price pulled back to EMA20, RSI oversold-ish
    if ef > es and pullback_dist < 1.5 and rsi_val < 45 and c > es:
        return SignalResult("BUY", c - _ATR_MULT * a, c + _TP_ATR * a,
                            _STRATEGY_TYPE, _OOS_WIN_RATE_PRIOR, _RR_RATIO,
                            _ATR_MULT * a, _TP_ATR * a)

    # SELL: downtrend, price rallied to EMA20, RSI overbought-ish
    if ef < es and pullback_dist < 1.5 and rsi_val > 55 and c < es:
        return SignalResult("SELL", c + _ATR_MULT * a, c - _TP_ATR * a,
                            _STRATEGY_TYPE, _OOS_WIN_RATE_PRIOR, _RR_RATIO,
                            _ATR_MULT * a, _TP_ATR * a)
    return _none()
