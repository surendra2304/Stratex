"""
strategy_adx_ema.py — ADX + EMA Trend Following & Pullback Engine

STRATEGY TYPE: RULE_BASED (deterministic, no probabilistic output)

Validated OOS statistics (used as structural expectancy priors by ProfitabilityGate):
  Win rate         : 0.494  (49.4% from multi-asset OOS benchmark)
  Risk:Reward      : 1:1.5  (2×ATR stop, 3×ATR target)
  ADX threshold    : 20
  EMA periods      : Fast=20, Slow=50, Direction=200
  ATR period       : 14 (for SL/TP sizing)
"""

from collections import namedtuple
import pandas as pd
import numpy as np

# ------------------------------------------------------------------
# Signal metadata — carried through execution pipeline
# ------------------------------------------------------------------
SignalResult = namedtuple(
    "SignalResult",
    ["side", "sl", "tp", "strategy_type", "win_rate_prior", "rr_ratio"]
)

# Structural parameters frozen from OOS validation
_STRATEGY_TYPE       = "RULE_BASED"
_OOS_WIN_RATE_PRIOR  = 0.494   # 49.4% — multi-asset OOS validated win rate
_RR_RATIO            = 1.5     # reward/risk: 3×ATR tp / 2×ATR sl


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
    ADX + EMA Trend Following & Dynamic Pullback Strategy.

    Rules:
      1. Crossover Breakout: EMA20 crosses above EMA50, close > EMA200, ADX > 20
      2. Established Trend Pullback: EMA20 > EMA50 > EMA200, ADX > 20, price dips to touch EMA20/50 & closes bullish
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
    trend_strong = (last['adx'] > 20)  # ADX strength threshold

    # 1. Fresh Golden Cross in Uptrend
    if cross_up and last['close'] > last['ema_200'] and trend_strong:
        sl = last['close'] - (2.0 * last['atr_adx_ema'])
        tp = last['close'] + (3.0 * last['atr_adx_ema'])
        return SignalResult("BUY", sl, tp, _STRATEGY_TYPE, _OOS_WIN_RATE_PRIOR, _RR_RATIO)

    # 2. Established Trend Pullback Entry
    trend_aligned = (last['ema_20'] > last['ema_50']) and (last['ema_50'] > last['ema_200']) and (last['close'] > last['ema_200'])
    if trend_aligned and trend_strong:
        touched_ema = (last['low'] <= last['ema_20'] * 1.002) or (prev['low'] <= prev['ema_20'] * 1.002)
        bullish_bar = (last['close'] > last['open']) and (last['close'] >= last['ema_20'])
        
        if touched_ema and bullish_bar:
            sl = last['close'] - (2.0 * last['atr_adx_ema'])
            tp = last['close'] + (3.0 * last['atr_adx_ema'])
            return SignalResult("BUY", sl, tp, _STRATEGY_TYPE, _OOS_WIN_RATE_PRIOR, _RR_RATIO)

    # Short / Bearish Crossover
    if cross_dn and last['close'] < last['ema_200'] and trend_strong:
        sl = last['close'] + (2.0 * last['atr_adx_ema'])
        tp = last['close'] - (3.0 * last['atr_adx_ema'])
        return SignalResult("SELL", sl, tp, _STRATEGY_TYPE, _OOS_WIN_RATE_PRIOR, _RR_RATIO)

    return _NO_SIGNAL
