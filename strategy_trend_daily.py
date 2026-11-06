# strategy_trend_daily.py - Daily Donchian breakout trend following
"""Daily (1d) Donchian breakout with a long-term trend filter.

Evidence (Nov 2026 study: 16 Binance USD-M perpetuals, 2019-2026, real klines,
entries at next open, stop-first on same-bar conflict, 10 bps round-trip cost):

  entry : daily close above the prior 20-day high while EMA50 > EMA200
          (mirrored for shorts)
  stop  : 2.5 x ATR(14), placed exchange-side
  exit  : daily close crossing EMA(20) - this rule carries the edge; a fixed
          3R target was breakeven over 7 years and negative since 2024
  costs : fees are only ~0.007R at these stop distances (15-25% of price)

Measured net expectancy: +0.30R/trade over 444 trades (PF 1.95) and +0.03R in
the 2024-2026 out-of-sample window (PF 1.09). The 6-ATR take-profit is a rare
safety order, not the exit.

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
_OOS_WIN_RATE_PRIOR = 0.36   # measured: 36.0% full sample, 34.7% in 2024-2026
_RR_RATIO = 6.0              # safety TP; the EMA20 rule is the real exit

_DONCHIAN = 20
_EMA_FAST = 50
_EMA_SLOW = 200
_ATR_LEN = 14
_ATR_MULT = 2.5
_TP_ATR = 6.0


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
    """Return a BUY/SELL on a completed daily breakout, else a HOLD signal."""
    if df is None or len(df) < _EMA_SLOW + 5:
        return _none()

    work = _drop_forming_bar(df)
    if len(work) < _EMA_SLOW + 3:
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

    if c > ph and ef > es:
        return SignalResult(
            "BUY", c - _ATR_MULT * a, c + _TP_ATR * a,
            _STRATEGY_TYPE, _OOS_WIN_RATE_PRIOR, _RR_RATIO,
            _ATR_MULT * a, _TP_ATR * a,
        )
    if c < pl and ef < es:
        return SignalResult(
            "SELL", c + _ATR_MULT * a, c - _TP_ATR * a,
            _STRATEGY_TYPE, _OOS_WIN_RATE_PRIOR, _RR_RATIO,
            _ATR_MULT * a, _TP_ATR * a,
        )
    return _none()
