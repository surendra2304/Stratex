"""
strategy_adx_ema_mtf.py — Multi-Timeframe (1h/15m) ADX + EMA Futures Strategy

ARCHITECTURE:
  - HTF (Higher Timeframe - 1h): Trend Filter
      * Long Bias:  1h EMA(20) > EMA(50) AND 1h Close > EMA(200) AND 1h ADX(14) > 20
      * Short Bias: 1h EMA(20) < EMA(50) AND 1h Close < EMA(200) AND 1h ADX(14) > 20
      * Neutral:    No trades allowed if 1h ADX <= 20 or EMAs are tangled.
  - LTF (Lower Timeframe - 15m): Sniper Entry Trigger
      * Long Trigger A (Crossover):  15m EMA(20) crosses above 15m EMA(50) while 1h Long Bias is active.
      * Long Trigger B (Retest):     First 15m bar touching 15m EMA(20) with a bullish close within 10 bars of a cross.
      * Short Trigger A (Crossover): 15m EMA(20) crosses below 15m EMA(50) while 1h Short Bias is active.
      * Short Trigger B (Retest):    First 15m bar touching 15m EMA(20) with a bearish close within 10 bars of a cross.
  - Exits & Risk Management (15m):
      * Stop Loss (SL):   1.5 × 15m ATR(14)
      * Take Profit (TP): 3.0 × 15m ATR(14) (Risk/Reward = 1:2)
"""

from collections import namedtuple

import numpy as np
import pandas as pd

from config_strategy import ADX_EMA_MTF_STRATEGY as _CFG


class SignalResult(namedtuple("SignalResult", ["side", "sl", "tp", "strategy_type", "win_rate_prior", "rr_ratio"])):
    @property
    def confidence(self):
        return self.win_rate_prior


_STRATEGY_TYPE      = "RULE_BASED"
_OOS_WIN_RATE_PRIOR = _CFG.get("OOS_WIN_RATE_PRIOR", 0.516)
_RR_RATIO           = _CFG.get("RISK_REWARD_RATIO", 1.33)
_ADX_THRESHOLD      = _CFG.get("ADX_THRESHOLD", 25)
_SL_ATR             = _CFG.get("SL_ATR_MULTIPLIER", 3.0)
_TP_ATR             = _CFG.get("TP_ATR_MULTIPLIER", 4.0)
_ENABLE_RETEST      = _CFG.get("ENABLE_RETEST_ENTRY", True)
_RETEST_WINDOW_BARS = _CFG.get("RETEST_WINDOW_BARS", 10)


def compute_atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """ATR(period) using Wilder's EWM smoothing."""
    tr = pd.concat([
        df['high'] - df['low'],
        abs(df['high'] - df['close'].shift(1)),
        abs(df['low']  - df['close'].shift(1))
    ], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / period, adjust=False).mean()


def compute_adx(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """Pure-pandas Wilder-smoothed ADX."""
    plus_dm  = df['high'].diff()
    minus_dm = -df['low'].diff()
    plus_dm  = np.where((plus_dm > minus_dm) & (plus_dm > 0), plus_dm, 0.0)
    minus_dm = np.where((minus_dm > plus_dm) & (minus_dm > 0), minus_dm, 0.0)

    tr = pd.concat([
        df['high'] - df['low'],
        abs(df['high'] - df['close'].shift(1)),
        abs(df['low']  - df['close'].shift(1))
    ], axis=1).max(axis=1)
    
    tr_smooth = tr.ewm(alpha=1 / period, adjust=False).mean()
    plus_di   = pd.Series(plus_dm,  index=df.index).ewm(alpha=1 / period, adjust=False).mean() / (tr_smooth + 1e-12) * 100
    minus_di  = pd.Series(minus_dm, index=df.index).ewm(alpha=1 / period, adjust=False).mean() / (tr_smooth + 1e-12) * 100
    dx        = (abs(plus_di - minus_di) / (plus_di + minus_di + 1e-12)) * 100
    return dx.ewm(alpha=1 / period, adjust=False).mean()


def add_features(df: pd.DataFrame) -> pd.DataFrame:
    """Adds EMA(20), EMA(50), EMA(200), ATR(14), and ADX(14) causal indicators."""
    df = df.copy()
    df['ema_20']  = df['close'].ewm(span=20,  adjust=False).mean()
    df['ema_50']  = df['close'].ewm(span=50,  adjust=False).mean()
    df['ema_200'] = df['close'].ewm(span=200, adjust=False).mean()
    df['atr']     = compute_atr(df, 14)
    df['adx']     = compute_adx(df, 14)
    return df


def get_htf_trend_bias(df_1h: pd.DataFrame) -> str:
    """
    Evaluates 1h Higher Timeframe Trend Bias.
    Returns: 'LONG', 'SHORT', or 'NEUTRAL'
    """
    if df_1h is None or len(df_1h) < 50:
        return "NEUTRAL"

    if 'ema_200' not in df_1h.columns or 'adx' not in df_1h.columns:
        df_1h = add_features(df_1h)

    last = df_1h.iloc[-1]
    
    # 1. Trend Strength Gate
    if pd.isna(last['adx']) or last['adx'] <= _ADX_THRESHOLD:
        return "NEUTRAL"

    # 2. Bullish Alignment
    if last['ema_20'] > last['ema_50'] and last['close'] > last['ema_200']:
        return "LONG"

    # 3. Bearish Alignment
    if last['ema_20'] < last['ema_50'] and last['close'] < last['ema_200']:
        return "SHORT"

    return "NEUTRAL"


def get_signal(df_5m: pd.DataFrame, df_1h: pd.DataFrame | None = None) -> SignalResult:
    """
    Multi-Timeframe ADX + EMA signal generator.
    
    Args:
        df_5m: 5m execution dataframe (required)
        df_1h: 1h trend filter dataframe (optional; if omitted, checks 5m trend directly)
        
    Returns:
        SignalResult with side ('BUY', 'SELL', None), sl price, tp price, and metadata.
    """
    _NO_SIGNAL = SignalResult(None, None, None, _STRATEGY_TYPE, _OOS_WIN_RATE_PRIOR, _RR_RATIO)

    if df_5m is None or len(df_5m) < 50:
        return _NO_SIGNAL

    # Ensure 5m features
    if 'atr' not in df_5m.columns or 'ema_50' not in df_5m.columns:
        df_5m = add_features(df_5m)

    last_5m = df_5m.iloc[-1]
    prev_5m = df_5m.iloc[-2]

    # Guard NaN in 5m features
    required = ['ema_20', 'ema_50', 'atr', 'adx']
    if any(pd.isna(last_5m[c]) for c in required):
        return _NO_SIGNAL

    atr_5m = last_5m['atr']
    if atr_5m <= 0:
        return _NO_SIGNAL

    # 1. Determine Trend Bias from 1h HTF (or fallback to 5m EMA200 if 1h not supplied)
    if df_1h is not None and not df_1h.empty:
        htf_bias = get_htf_trend_bias(df_1h)
    else:
        # Fallback to local 5m bias if HTF not provided
        if last_5m['close'] > last_5m['ema_200'] and last_5m['adx'] > _ADX_THRESHOLD:
            htf_bias = "LONG"
        elif last_5m['close'] < last_5m['ema_200'] and last_5m['adx'] > _ADX_THRESHOLD:
            htf_bias = "SHORT"
        else:
            htf_bias = "NEUTRAL"

    if htf_bias == "NEUTRAL":
        return _NO_SIGNAL

    cross_up = (last_5m['ema_20'] > last_5m['ema_50']) and (prev_5m['ema_20'] <= prev_5m['ema_50'])
    cross_dn = (last_5m['ema_20'] < last_5m['ema_50']) and (prev_5m['ema_20'] >= prev_5m['ema_50'])

    # 2. LONG Signal Evaluation
    if htf_bias == "LONG":
        # Entry A: Crossover in trend direction
        if cross_up:
            sl = last_5m['close'] - (_SL_ATR * atr_5m)
            tp = last_5m['close'] + (_TP_ATR * atr_5m)
            return SignalResult("BUY", sl, tp, _STRATEGY_TYPE, _OOS_WIN_RATE_PRIOR, _RR_RATIO)

        # Entry B: Qualified Retest within window
        if _ENABLE_RETEST and last_5m['ema_20'] > last_5m['ema_50']:
            if last_5m['low'] <= last_5m['ema_20'] * 1.001 and last_5m['close'] > last_5m['open'] and last_5m['close'] >= last_5m['ema_20']:
                li = len(df_5m) - 1
                for back in range(1, _RETEST_WINDOW_BARS + 1):
                    k = li - back
                    if k < 1:
                        break
                    bar, before = df_5m.iloc[k], df_5m.iloc[k - 1]
                    if (bar['ema_20'] > bar['ema_50']) and (before['ema_20'] <= before['ema_50']):
                        between = df_5m.iloc[k + 1:li]
                        if (between['low'] > between['ema_20'] * 1.001).all():
                            sl = last_5m['close'] - (_SL_ATR * atr_5m)
                            tp = last_5m['close'] + (_TP_ATR * atr_5m)
                            return SignalResult("BUY", sl, tp, _STRATEGY_TYPE, _OOS_WIN_RATE_PRIOR, _RR_RATIO)
                        break

    # 3. SHORT Signal Evaluation
    elif htf_bias == "SHORT":
        # Entry A: Crossover in trend direction
        if cross_dn:
            sl = last_5m['close'] + (_SL_ATR * atr_5m)
            tp = last_5m['close'] - (_TP_ATR * atr_5m)
            return SignalResult("SELL", sl, tp, _STRATEGY_TYPE, _OOS_WIN_RATE_PRIOR, _RR_RATIO)

        # Entry B: Qualified Retest within window
        if _ENABLE_RETEST and last_5m['ema_20'] < last_5m['ema_50']:
            if last_5m['high'] >= last_5m['ema_20'] * 0.999 and last_5m['close'] < last_5m['open'] and last_5m['close'] <= last_5m['ema_20']:
                li = len(df_5m) - 1
                for back in range(1, _RETEST_WINDOW_BARS + 1):
                    k = li - back
                    if k < 1:
                        break
                    bar, before = df_5m.iloc[k], df_5m.iloc[k - 1]
                    if (bar['ema_20'] < bar['ema_50']) and (before['ema_20'] >= before['ema_50']):
                        between = df_5m.iloc[k + 1:li]
                        if (between['high'] < between['ema_20'] * 0.999).all():
                            sl = last_5m['close'] + (_SL_ATR * atr_5m)
                            tp = last_5m['close'] - (_TP_ATR * atr_5m)
                            return SignalResult("SELL", sl, tp, _STRATEGY_TYPE, _OOS_WIN_RATE_PRIOR, _RR_RATIO)
                        break

    return _NO_SIGNAL
