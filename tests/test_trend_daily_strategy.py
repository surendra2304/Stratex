"""Unit tests for strategy_trend_daily (daily Donchian breakout trend follow)."""
import math

import numpy as np
import pandas as pd
import pytest

import strategy_trend_daily as std


def _make_df(close, seed=7):
    rng = np.random.default_rng(seed)
    n = len(close)
    close = np.asarray(close, dtype=float)
    high = close + rng.uniform(0.5, 1.5, n)
    low = close - rng.uniform(0.5, 1.5, n)
    op = close - rng.uniform(-0.5, 0.5, n)
    vol = rng.uniform(100, 200, n)
    idx = pd.date_range("2020-01-01", periods=n, freq="D")
    return pd.DataFrame(
        {"open": op, "high": high, "low": low, "close": close, "volume": vol},
        index=idx,
    )


def _uptrend(n=260, seed=1):
    rng = np.random.default_rng(seed)
    base = np.linspace(100.0, 200.0, n)
    close = base + rng.normal(0, 0.3, n)
    df = _make_df(close, seed=seed)
    # force a clean breakout on the last bar
    ph = df["high"].iloc[-21:-1].max()
    df.loc[df.index[-1], "close"] = ph + 5.0
    df.loc[df.index[-1], "high"] = ph + 6.0
    df.loc[df.index[-1], "low"] = ph + 3.0
    return df


def _downtrend(n=260, seed=2):
    rng = np.random.default_rng(seed)
    base = np.linspace(200.0, 100.0, n)
    close = base + rng.normal(0, 0.3, n)
    df = _make_df(close, seed=seed)
    pl = df["low"].iloc[-21:-1].min()
    df.loc[df.index[-1], "close"] = pl - 5.0
    df.loc[df.index[-1], "low"] = pl - 6.0
    df.loc[df.index[-1], "high"] = pl - 3.0
    return df


def _ref(df):
    """Independent reference implementation of the documented logic."""
    if df is None or len(df) < std._EMA_SLOW + 5:
        return None
    work = df
    close = work["close"].astype(float)
    high = work["high"].astype(float)
    low = work["low"].astype(float)
    ef = close.ewm(span=std._EMA_FAST, adjust=False).mean().iloc[-1]
    es = close.ewm(span=std._EMA_SLOW, adjust=False).mean().iloc[-1]
    prev = close.shift(1)
    tr = pd.concat([high - low, (high - prev).abs(), (low - prev).abs()], axis=1).max(axis=1)
    a = tr.rolling(std._ATR_LEN).mean().iloc[-1]
    ph = high.rolling(std._DONCHIAN).max().shift(1).iloc[-1]
    pl = low.rolling(std._DONCHIAN).min().shift(1).iloc[-1]
    c = float(close.iloc[-1])
    if any(isinstance(v, float) and math.isnan(v) for v in (a, ph, pl, ef, es)):
        return None
    if a <= 0 or c <= 0:
        return None
    if c > ph and ef > es:
        return ("BUY", c - std._ATR_MULT * a, c + std._TP_ATR * a)
    if c < pl and ef < es:
        return ("SELL", c + std._ATR_MULT * a, c - std._TP_ATR * a)
    return None


def test_uptrend_breakout_buy():
    df = _uptrend()
    sig = std.get_signal(df)
    assert sig.side == "BUY"
    c = float(df["close"].iloc[-1])
    a = float(
        pd.concat(
            [
                df["high"] - df["low"],
                (df["high"] - df["close"].shift(1)).abs(),
                (df["low"] - df["close"].shift(1)).abs(),
            ],
            axis=1,
        )
        .max(axis=1)
        .rolling(14)
        .mean()
        .iloc[-1]
    )
    assert sig.sl == pytest.approx(c - 2.5 * a, abs=1e-6)
    assert sig.tp == pytest.approx(c + 6.0 * a, abs=1e-6)
    assert sig.sl < c < sig.tp


def test_downtrend_breakdown_sell():
    df = _downtrend()
    sig = std.get_signal(df)
    assert sig.side == "SELL"
    c = float(df["close"].iloc[-1])
    assert sig.tp < c < sig.sl


def test_uptrend_no_breakout_none():
    df = _uptrend()
    # flatten last bar so it no longer breaks out
    ph = df["high"].iloc[-21:-1].max()
    df.loc[df.index[-1], "close"] = ph - 1.0
    df.loc[df.index[-1], "high"] = ph - 0.5
    sig = std.get_signal(df)
    assert sig.side is None


def test_breakout_against_trend_none():
    n = 260
    rng = np.random.default_rng(3)
    base = np.linspace(200.0, 100.0, n)
    close = base + rng.normal(0, 0.3, n)
    df = _make_df(close, seed=3)
    # last bar breaks above prior 20 high
    ph = df["high"].iloc[-21:-1].max()
    df.loc[df.index[-1], "close"] = ph + 5.0
    df.loc[df.index[-1], "high"] = ph + 6.0
    df.loc[df.index[-1], "low"] = ph + 3.0
    sig = std.get_signal(df)
    assert sig.side is None


def test_insufficient_history_none():
    df = _uptrend(n=100)
    assert std.get_signal(df).side is None


def test_forming_bar_ignored():
    base = _uptrend(n=259)
    # make base last bar a non-breakout plateau
    ph_base = base["high"].iloc[-21:-1].max()
    base.loc[base.index[-1], "close"] = ph_base - 1.0
    base.loc[base.index[-1], "high"] = ph_base - 0.5

    # tz-aware UTC index for completed bars
    now = pd.Timestamp.now(tz="UTC")
    base.index = pd.date_range(end=now.normalize() - pd.Timedelta(days=1), periods=len(base), freq="D", tz="UTC")

    # today bar that would trigger a breakout
    ph = base["high"].iloc[-20:].max()
    today_idx = now.normalize()
    today_row = pd.DataFrame(
        {
            "open": [ph + 4.0],
            "high": [ph + 6.0],
            "low": [ph + 3.0],
            "close": [ph + 5.0],
            "volume": [150.0],
        },
        index=pd.DatetimeIndex([today_idx], tz="UTC"),
    )
    full = pd.concat([base, today_row])

    # dropped -> equals previous completed bar's signal
    assert std.get_signal(full) == std.get_signal(base)

    # if not dropped (index shifted back), it triggers
    shifted = full.copy()
    shifted.index = pd.date_range(end=now.normalize() - pd.Timedelta(days=1), periods=len(full), freq="D", tz="UTC")
    assert std.get_signal(shifted).side == "BUY"


def test_stop_distance_atr_multiple():
    df = _uptrend()
    sig = std.get_signal(df)
    close = df["close"].astype(float)
    high = df["high"].astype(float)
    low = df["low"].astype(float)
    prev = close.shift(1)
    tr = pd.concat([high - low, (high - prev).abs(), (low - prev).abs()], axis=1).max(axis=1)
    a = float(tr.rolling(14).mean().iloc[-1])
    c = float(close.iloc[-1])
    assert abs((c - sig.sl) - 2.5 * a) < 1e-6


def test_no_lookahead():
    df = _uptrend(n=260)
    # ensure deterministic non-trivial: use as is
    for i in (205, 210, 220, 240, 259):
        sub = df.iloc[: i + 1]
        got = std.get_signal(sub)
        ref = _ref(sub)
        if ref is None:
            assert got.side is None
        else:
            assert got.side == ref[0]
            assert got.sl == pytest.approx(ref[1], abs=1e-6)
            assert got.tp == pytest.approx(ref[2], abs=1e-6)


if __name__ == "__main__":
    pass
