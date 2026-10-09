# strategy_hybrid.py - Hybrid EMA + ADX strategy
# Placeholder implementation following similar pattern to existing strategies.

import math
from collections import namedtuple

from numeric_safety import signal_levels_valid


class SignalResult(namedtuple("SignalResult", ["side", "sl", "tp", "strategy_type", "win_rate_prior", "rr_ratio"])):
    @property
    def confidence(self):
        return self.win_rate_prior

_STRATEGY_TYPE = "RULE_BASED"
_OOS_WIN_RATE_PRIOR = 0.52  # Expected win rate based on OOS testing
_RR_RATIO = 2.0

def get_signal(df):
    """Hybrid strategy combining EMA crossover and ADX momentum.
    BUY when EMA(20) > EMA(50) and ADX(14) > 25 and price is above EMA(200).
    SELL when EMA(20) < EMA(50) and ADX(14) > 25 and price is below EMA(200).
    Returns a SignalResult or None fields if conditions not met.
    """
    if df is None or len(df) < 20:
        return SignalResult(None, None, None, _STRATEGY_TYPE, _OOS_WIN_RATE_PRIOR, _RR_RATIO)
    if "ema_200" not in df.columns or "adx" not in df.columns:
        try:
            import features
            df = features.add_features(df)
            if "adx" not in df.columns:
                from strategy_adx_ema import compute_adx
                df["adx"] = compute_adx(df, 14)
        except Exception:
            return SignalResult(None, None, None, _STRATEGY_TYPE, _OOS_WIN_RATE_PRIOR, _RR_RATIO)

    last = df.iloc[-1]
    if "ema_200" not in last:
        return SignalResult(None, None, None, _STRATEGY_TYPE, _OOS_WIN_RATE_PRIOR, _RR_RATIO)

    ema20 = float(last.get("ema_20", last.get("ema_21", last["close"])))
    ema50 = float(last.get("ema_50", last["close"]))
    ema200 = float(last.get("ema_200", last["close"]))
    adx = float(last.get("adx", last.get("adx_14", 25.0)))
    close = float(last["close"])
    atr = float(last.get("atr", last.get("atr_14", close * 0.01)))
    no_signal = SignalResult(None, None, None, _STRATEGY_TYPE, _OOS_WIN_RATE_PRIOR, _RR_RATIO)
    if not all(math.isfinite(v) for v in (ema20, ema50, ema200, adx, close, atr)) or close <= 0 or atr <= 0:
        return no_signal  # undefined indicators or zero volatility: no protective levels exist
    if ema20 > ema50 and adx > 25 and close > ema200:
        sl = close - atr * 1.5
        tp = close + atr * 3.0
        if signal_levels_valid("BUY", close, sl, tp):
            return SignalResult("BUY", sl, tp, _STRATEGY_TYPE, _OOS_WIN_RATE_PRIOR, _RR_RATIO)
    if ema20 < ema50 and adx > 25 and close < ema200:
        sl = close + atr * 1.5
        tp = close - atr * 3.0
        if signal_levels_valid("SELL", close, sl, tp):
            return SignalResult("SELL", sl, tp, _STRATEGY_TYPE, _OOS_WIN_RATE_PRIOR, _RR_RATIO)
    return no_signal
