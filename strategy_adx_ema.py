"""
strategy_adx_ema.py — ADX + EMA Trend Following Engine (V2)

STRATEGY TYPE: RULE_BASED (deterministic, no probabilistic output)

V2 (2026-08-22 profitability upgrade — see config_strategy.ADX_EMA_STRATEGY_V2):
  Win rate         : 0.60  (V2 multi-asset OOS benchmark 2024-2026)
  Risk:Reward      : 1:1.0 (3×ATR stop, 3×ATR target)
  ADX threshold    : 30
  EMA periods      : Fast=20, Slow=50, Direction=200
  ATR period       : 14 (for SL/TP sizing)
  Pullback entry   : REMOVED — net-negative across 2021-2026 (PF 0.85 OOS with it on)
All runtime parameters are sourced from ADX_EMA_STRATEGY_V2.
"""

from collections import namedtuple

import numpy as np
import pandas as pd

from config_strategy import ADX_EMA_STRATEGY_V2 as _CFG

# ------------------------------------------------------------------
# Signal metadata — carried through execution pipeline
# ------------------------------------------------------------------
class SignalResult(namedtuple("SignalResult", ["side", "sl", "tp", "strategy_type", "win_rate_prior", "rr_ratio"])):
    @property
    def confidence(self):
        return self.win_rate_prior

# Structural parameters sourced from the V2 validated config
_STRATEGY_TYPE       = "RULE_BASED"
_OOS_WIN_RATE_PRIOR  = _CFG["OOS_WIN_RATE_PRIOR"]   # 0.60 — V2 multi-asset OOS validated
_RR_RATIO            = _CFG["RISK_REWARD_RATIO"]    # 3×ATR tp / 3×ATR sl
_ADX_THRESHOLD       = _CFG["ADX_THRESHOLD"]        # 30
_SL_ATR              = _CFG["SL_ATR_MULTIPLIER"]    # 3.0
_TP_ATR              = _CFG["TP_ATR_MULTIPLIER"]    # 3.0
_ENABLE_PULLBACK     = _CFG["ENABLE_PULLBACK_ENTRY"]  # False — net-negative 2021-2026
_ENABLE_RETEST       = _CFG["ENABLE_RETEST_ENTRY"]    # True — rev 3 qualified retest
_RETEST_WINDOW_BARS  = _CFG["RETEST_WINDOW_BARS"]     # 10 bars to arm after a cross


def compute_atr(df, period=14):
    """ATR(period) using EWM smoothing — identical to benchmark."""
    tr = pd.concat([
        df['high'] - df['low'],
        abs(df['high'] - df['close'].shift(1)),
        abs(df['low']  - df['close'].shift(1))
    ], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / period, adjust=False).mean()


def _true_range(df):
    return pd.concat([
        df['high'] - df['low'],
        abs(df['high'] - df['close'].shift(1)),
        abs(df['low']  - df['close'].shift(1))
    ], axis=1).max(axis=1)


def compute_adx(df, period=14):
    """Pure-pandas Wilder-smoothed ADX — identical to benchmark."""
    plus_dm  = df['high'].diff()
    minus_dm = df['low'].diff(-1).shift(1)
    plus_dm  = np.where((plus_dm  > minus_dm) & (plus_dm  > 0), plus_dm,  0.0)
    minus_dm = np.where((minus_dm > plus_dm)  & (minus_dm > 0), minus_dm, 0.0)

    tr         = _true_range(df)
    tr_smooth  = tr.ewm(alpha=1 / period, adjust=False).mean()
    plus_di    = pd.Series(plus_dm,  index=df.index).ewm(alpha=1 / period, adjust=False).mean() / tr_smooth * 100
    minus_di   = pd.Series(minus_dm, index=df.index).ewm(alpha=1 / period, adjust=False).mean() / tr_smooth * 100
    dx         = (abs(plus_di - minus_di) / (plus_di + minus_di + 1e-9)) * 100
    return dx.ewm(alpha=1 / period, adjust=False).mean()


def add_features(df):
    """
    Computes all strategy-specific features in-place.
    Called by the execution engine prior to get_signal().
    All computations are strictly causal (no look-ahead).
    """
    df = df.copy()
    df['ema_20']      = df['close'].ewm(span=20,  adjust=False).mean()
    df['ema_50']      = df['close'].ewm(span=50,  adjust=False).mean()
    df['ema_200']     = df['close'].ewm(span=200, adjust=False).mean()
    df['atr_adx_ema'] = compute_atr(df, 14)
    df['adx']         = compute_adx(df, 14)
    return df


def get_signal(df):
    """
    ADX + EMA Trend Following Strategy (V2 rev3 — crossover + qualified retest).

    Rules:
      1. Crossover Breakout: EMA20 crosses above EMA50, close > EMA200, ADX > threshold
      2. Qualified Retest (rev 3): if a qualified golden cross fired within the
         last RETEST_WINDOW_BARS bars and price has NOT touched EMA20 since,
         enter on the first bar whose low touches EMA20 and closes bullish
         above it. Adds ~55% OOS trades at higher PF (see
         research/upgrade_2026_08/expansion_study.py Study C).
      3. Short / Bearish Crossover: mirror of rule 1

    The V1 "Established Trend Pullback" entry was REMOVED in V2: it is
    net-negative across 2021-2026 under full friction (see
    research/upgrade_2026_08/param_study.py).
    """
    _NO_SIGNAL = SignalResult(None, None, None, _STRATEGY_TYPE, _OOS_WIN_RATE_PRIOR, _RR_RATIO)

    if df is None or len(df) < 50:
        return _NO_SIGNAL

    if 'atr_adx_ema' not in df.columns or 'adx' not in df.columns:
        df = add_features(df)

    last = df.iloc[-1]
    prev = df.iloc[-2]

    # Guard NaN
    required = ['ema_20', 'ema_50', 'ema_200', 'atr_adx_ema', 'adx']
    if any(pd.isna(last[c]) for c in required):
        return _NO_SIGNAL

    cross_up = (last['ema_20'] > last['ema_50']) and (prev['ema_20'] <= prev['ema_50'])
    cross_dn = (last['ema_20'] < last['ema_50']) and (prev['ema_20'] >= prev['ema_50'])
    trend_strong = (last['adx'] > _ADX_THRESHOLD)

    def _buy():
        sl = last['close'] - (_SL_ATR * last['atr_adx_ema'])
        tp = last['close'] + (_TP_ATR * last['atr_adx_ema'])
        return SignalResult("BUY", sl, tp, _STRATEGY_TYPE, _OOS_WIN_RATE_PRIOR, _RR_RATIO)

    # 1. Fresh Golden Cross in Uptrend
    if cross_up and last['close'] > last['ema_200'] and trend_strong:
        return _buy()

    # V1 pullback entry removed — historically net-negative (PF 0.85 OOS with it enabled)

    # 2. Qualified Retest entry (rev 3) — BUY only; the service's BTC-regime
    #    gate applies to every BUY signal downstream.
    if _ENABLE_RETEST and trend_strong and last['close'] > last['ema_200'] and last['ema_20'] > last['ema_50']:
        if last['low'] <= last['ema_20'] * 1.002 and last['close'] > last['open'] and last['close'] >= last['ema_20']:
            li = len(df) - 1
            for back in range(1, _RETEST_WINDOW_BARS + 1):
                k = li - back  # candidate: golden cross printed AT bar k
                if k < 1:
                    break
                bar, before = df.iloc[k], df.iloc[k - 1]
                if (bar['ema_20'] > bar['ema_50']) and (before['ema_20'] <= before['ema_50']):
                    if bar['close'] > bar['ema_200'] and bar['adx'] > _ADX_THRESHOLD:
                        between = df.iloc[k + 1:li]  # bars after the cross, before now
                        if (between['low'] > between['ema_20'] * 1.002).all():
                            return _buy()
                    break  # most recent cross inside window found — no earlier one matters

    # 3. Short / Bearish Crossover
    if cross_dn and last['close'] < last['ema_200'] and trend_strong:
        sl = last['close'] + (_SL_ATR * last['atr_adx_ema'])
        tp = last['close'] - (_TP_ATR * last['atr_adx_ema'])
        return SignalResult("SELL", sl, tp, _STRATEGY_TYPE, _OOS_WIN_RATE_PRIOR, _RR_RATIO)

    return _NO_SIGNAL
