# strategy_momentum.py - RSI + MACD momentum continuation
"""Momentum strategy based on RSI and MACD convergence.
- BUY when RSI crosses above 50 with MACD bullish and price above EMA50/EMA200
- SELL when RSI crosses below 50 with MACD bearish and price below EMA50/EMA200
- Stop: 2.0 x ATR(14), safety TP: 5.0 x ATR(14)
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
_OOS_WIN_RATE_PRIOR = 0.40
_RR_RATIO = 5.0
_ATR_MULT = 2.0
_TP_ATR = 5.0


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

    # MACD
    ema12 = close.ewm(span=12, adjust=False).mean()
    ema26 = close.ewm(span=26, adjust=False).mean()
    macd = ema12 - ema26
    macd_signal = macd.ewm(span=9, adjust=False).mean()
    macd_hist = macd - macd_signal

    # ATR
    prev_close = close.shift(1)
    tr = pd.concat([high - low, (high - prev_close).abs(), (low - prev_close).abs()], axis=1).max(axis=1)
    atr = tr.rolling(14).mean()

    c = float(close.iloc[-1])
    a = float(atr.iloc[-1])
    ef = float(ema_fast.iloc[-1])
    es = float(ema_slow.iloc[-1])
    rsi_val = float(rsi.iloc[-1])
    rsi_prev = float(rsi.iloc[-2])
    macd_h = float(macd_hist.iloc[-1])
    macd_h_prev = float(macd_hist.iloc[-2])

    if any(math.isnan(v) for v in (a, ef, es, rsi_val)) or a <= 0 or c <= 0:
        return _none()

    # BUY: RSI crosses above 50, MACD histogram positive and increasing, trend up
    rsi_cross_up = rsi_prev <= 50 < rsi_val
    macd_bullish = macd_h > 0 and macd_h > macd_h_prev
    trend_up = ef > es

    # SELL: RSI crosses below 50, MACD histogram negative and decreasing, trend down
    rsi_cross_down = rsi_prev >= 50 > rsi_val
    macd_bearish = macd_h < 0 and macd_h < macd_h_prev
    trend_down = ef < es

    if rsi_cross_up and macd_bullish and trend_up:
        return SignalResult("BUY", c - _ATR_MULT * a, c + _TP_ATR * a,
                            _STRATEGY_TYPE, _OOS_WIN_RATE_PRIOR, _RR_RATIO,
                            _ATR_MULT * a, _TP_ATR * a)
    if rsi_cross_down and macd_bearish and trend_down:
        return SignalResult("SELL", c + _ATR_MULT * a, c - _TP_ATR * a,
                            _STRATEGY_TYPE, _OOS_WIN_RATE_PRIOR, _RR_RATIO,
                            _ATR_MULT * a, _TP_ATR * a)
    return _none()
