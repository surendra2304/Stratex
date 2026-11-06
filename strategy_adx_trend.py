# strategy_adx_trend.py - ADX Trend Strength
"""ADX(14) trend-strength filter with EMA trend alignment.

Entry: ADX(14) > 25 and +DI > -DI and price > EMA50 with EMA50 > EMA200 (longs);
       mirrored for shorts. Stop 2.5 x ATR(14), safety TP 5.0 x ATR(14).
Exit : EMA(20) cross (handled downstream).
"""

from __future__ import annotations

import math
from collections import namedtuple

import numpy as np
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
_RR_RATIO = 5.0

_ADX_LEN = 14
_ADX_THRESHOLD = 25
_EMA_FAST = 50
_EMA_SLOW = 200
_ATR_LEN = 14
_ATR_MULT_STOP = 2.5
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


def _col(work, *names):
    for n in names:
        if n in work.columns:
            return work[n].astype(float)
    return None


def _compute_adx_di(high, low, close, period=14):
    """Manual Wilder-smoothed ADX with +DI/-DI using pandas (no external lib)."""
    plus_dm = high.diff()
    minus_dm = -low.diff()
    plus_dm = pd.Series(
        np.where((plus_dm > minus_dm) & (plus_dm > 0), plus_dm, 0.0),
        index=high.index,
    )
    minus_dm = pd.Series(
        np.where((minus_dm > plus_dm) & (minus_dm > 0), minus_dm, 0.0),
        index=high.index,
    )

    tr = pd.concat(
        [high - low, (high - close.shift(1)).abs(), (low - close.shift(1)).abs()],
        axis=1,
    ).max(axis=1)
    tr_smooth = tr.ewm(alpha=1 / period, adjust=False).mean()
    plus_di = plus_dm.ewm(alpha=1 / period, adjust=False).mean() / (tr_smooth + 1e-12) * 100
    minus_di = minus_dm.ewm(alpha=1 / period, adjust=False).mean() / (tr_smooth + 1e-12) * 100
    dx = (plus_di - minus_di).abs() / (plus_di + minus_di + 1e-12) * 100
    adx = dx.ewm(alpha=1 / period, adjust=False).mean()
    return adx, plus_di, minus_di


def compute_atr(df, period=14):
    close = df["close"].astype(float)
    high = df["high"].astype(float)
    low = df["low"].astype(float)
    prev = close.shift(1)
    tr = pd.concat(
        [high - low, (high - prev).abs(), (low - prev).abs()], axis=1
    ).max(axis=1)
    return tr.rolling(period).mean()


def compute_adx(df, period=14):
    close = df["close"].astype(float)
    high = df["high"].astype(float)
    low = df["low"].astype(float)
    adx, _p, _m = _compute_adx_di(high, low, close, period)
    return adx


def add_features(df):
    df = df.copy()
    close = df["close"].astype(float)
    high = df["high"].astype(float)
    low = df["low"].astype(float)
    df["ema_20"] = close.ewm(span=20, adjust=False).mean()
    df["ema_50"] = close.ewm(span=50, adjust=False).mean()
    df["ema_200"] = close.ewm(span=200, adjust=False).mean()
    df["atr"] = compute_atr(df, _ATR_LEN)
    adx, plus_di, minus_di = _compute_adx_di(high, low, close, _ADX_LEN)
    df["adx"] = adx
    df["plus_di"] = plus_di
    df["minus_di"] = minus_di
    return df


def get_signal(df: pd.DataFrame):
    """Return a BUY/SELL on ADX trend strength, else a HOLD signal."""
    if df is None or len(df) < _MIN_BARS:
        return _none()

    work = _drop_forming_bar(df)
    if len(work) < _MIN_BARS - 3:
        return _none()

    required = {"close", "high", "low"}
    if not required.issubset(work.columns):
        return _none()

    close = work["close"].astype(float)
    high = work["high"].astype(float)
    low = work["low"].astype(float)

    # EMA
    ema50_series = _col(work, "ema_50", "ema50", "ema_fast")
    if ema50_series is None:
        ema50_series = close.ewm(span=_EMA_FAST, adjust=False).mean()
    ema200_series = _col(work, "ema_200", "ema200", "ema_slow")
    if ema200_series is None:
        ema200_series = close.ewm(span=_EMA_SLOW, adjust=False).mean()

    # ATR (rolling mean like trend_daily)
    atr_series = _col(work, "atr", "atr_14", "atr_adx_ema")
    if atr_series is None:
        prev_close = close.shift(1)
        tr = pd.concat(
            [high - low, (high - prev_close).abs(), (low - prev_close).abs()],
            axis=1,
        ).max(axis=1)
        atr_series = tr.rolling(_ATR_LEN).mean()

    # ADX + DI
    adx_series = _col(work, "adx", "adx_14", "ADX")
    plus_series = _col(work, "plus_di", "plusdi", "+di", "pdi", "+DI", "plus_dm")
    minus_series = _col(work, "minus_di", "minusdi", "-di", "mdi", "-DI", "minus_dm")

    if adx_series is None or plus_series is None or minus_series is None:
        adx_c, plus_c, minus_c = _compute_adx_di(high, low, close, _ADX_LEN)
        if adx_series is None:
            adx_series = adx_c
        if plus_series is None:
            plus_series = plus_c
        if minus_series is None:
            minus_series = minus_c

    c = float(close.iloc[-1])
    a = float(atr_series.iloc[-1])
    ef = float(ema50_series.iloc[-1])
    es = float(ema200_series.iloc[-1])
    adx = float(adx_series.iloc[-1])
    pdi = float(plus_series.iloc[-1])
    mdi = float(minus_series.iloc[-1])

    if any(math.isnan(v) for v in (a, ef, es, adx, pdi, mdi)) or a <= 0 or c <= 0:
        return _none()

    sl_dist = _ATR_MULT_STOP * a
    tp_dist = _TP_ATR * a

    if adx > _ADX_THRESHOLD and pdi > mdi and c > ef and ef > es:
        return SignalResult(
            "BUY", c - sl_dist, c + tp_dist,
            _STRATEGY_TYPE, _OOS_WIN_RATE_PRIOR, _RR_RATIO,
            sl_dist, tp_dist,
        )
    if adx > _ADX_THRESHOLD and mdi > pdi and c < ef and ef < es:
        return SignalResult(
            "SELL", c + sl_dist, c - tp_dist,
            _STRATEGY_TYPE, _OOS_WIN_RATE_PRIOR, _RR_RATIO,
            sl_dist, tp_dist,
        )
    return _none()
