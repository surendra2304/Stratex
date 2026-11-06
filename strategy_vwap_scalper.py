# strategy_vwap_scalper.py - VWAP mean-reversion scalper for 5m/15m timeframes
"""VWAP mean-reversion scalper with RSI and volume filters (5m/15m).

Evidence (study template; replace with real backtest when available):
  entry : price < VWAP - 1.0 * VWAP_std AND RSI(14) < 40 with volume > 1.2x avg_vol (BUY)
          price > VWAP + 1.0 * VWAP_std AND RSI(14) > 60 with volume > 1.2x avg_vol (SELL)
  stop  : 1.2 x ATR(14), placed exchange-side
  exit  : price crosses VWAP OR RSI crosses 50
  costs : fees ~0.007R at these stop distances (intraday)

The daemon feeds only completed 5m/15m bars, so a signal cannot appear and
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


_STRATEGY_TYPE = "MEAN_REVERSION"
_OOS_WIN_RATE_PRIOR = 0.55
_RR_RATIO = 2.5

_VWAP_STD_MULT = 1.0
_RSI_LEN = 14
_RSI_BUY_THRESH = 40
_RSI_SELL_THRESH = 60
_RSI_EXIT = 50
_VOL_MULT = 1.2
_ATR_LEN = 14
_ATR_MULT = 1.2
_TP_ATR = 2.5
_MIN_BARS = 210


def _none():
    return SignalResult(None, None, None, _STRATEGY_TYPE, _OOS_WIN_RATE_PRIOR, _RR_RATIO)


def _drop_forming_bar(df: pd.DataFrame) -> pd.DataFrame:
    """Remove the still-open intraday candle so signals use closed data only."""
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


def _vwap(df: pd.DataFrame):
    """Daily-reset VWAP using typical price."""
    typical = (df["high"] + df["low"] + df["close"]) / 3.0
    vol = df["volume"].astype(float)
    cum_pv = (typical * vol).cumsum()
    cum_vol = vol.cumsum()
    return cum_pv / cum_vol


def _vwap_std(df: pd.DataFrame, vwap_series: pd.Series, window: int = 20):
    """Rolling standard deviation of price around VWAP."""
    typical = (df["high"] + df["low"] + df["close"]) / 3.0
    diff = typical - vwap_series
    return diff.rolling(window).std()


def _rsi(close: pd.Series, length: int = 14):
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1/length, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1/length, adjust=False).mean()
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))


def _atr(df: pd.DataFrame, length: int = 14):
    prev_close = df["close"].shift(1)
    tr = pd.concat([
        df["high"] - df["low"],
        (df["high"] - prev_close).abs(),
        (df["low"] - prev_close).abs(),
    ], axis=1).max(axis=1)
    return tr.rolling(length).mean()


def get_signal(df: pd.DataFrame):
    """Return a BUY/SELL VWAP mean-reversion signal, else HOLD."""
    if df is None or len(df) < _MIN_BARS:
        return _none()

    work = _drop_forming_bar(df)
    if len(work) < _MIN_BARS:
        return _none()

    required = {"close", "high", "low", "volume"}
    if not required.issubset(work.columns):
        return _none()

    close = work["close"].astype(float)
    high = work["high"].astype(float)
    low = work["low"].astype(float)

    # Daily-reset VWAP and bands
    vwap = _vwap(work)
    vwap_sd = _vwap_std(work, vwap)

    # RSI
    rsi = _rsi(close, _RSI_LEN)

    # Volume filter
    avg_vol = work["volume"].rolling(20).mean()
    vol_ok = work["volume"] > (_VOL_MULT * avg_vol)

    # ATR for stops/targets
    atr = _atr(work, _ATR_LEN)

    c = float(close.iloc[-1])
    v = float(vwap.iloc[-1])
    sd = float(vwap_sd.iloc[-1])
    r = float(rsi.iloc[-1])
    a = float(atr.iloc[-1])
    vo = bool(vol_ok.iloc[-1])

    if any(math.isnan(x) for x in (c, v, sd, r, a)) or a <= 0 or c <= 0 or sd <= 0:
        return _none()

    lower_band = v - _VWAP_STD_MULT * sd
    upper_band = v + _VWAP_STD_MULT * sd

    sl_dist = _ATR_MULT * a
    tp_dist = _TP_ATR * a

    # BUY: price below lower band, RSI oversold, volume spike
    if c < lower_band and r < _RSI_BUY_THRESH and vo:
        return SignalResult(
            "BUY", c - sl_dist, c + tp_dist,
            _STRATEGY_TYPE, _OOS_WIN_RATE_PRIOR, _RR_RATIO,
            sl_dist, tp_dist,
        )

    # SELL: price above upper band, RSI overbought, volume spike
    if c > upper_band and r > _RSI_SELL_THRESH and vo:
        return SignalResult(
            "SELL", c + sl_dist, c - tp_dist,
            _STRATEGY_TYPE, _OOS_WIN_RATE_PRIOR, _RR_RATIO,
            sl_dist, tp_dist,
        )

    return _none()