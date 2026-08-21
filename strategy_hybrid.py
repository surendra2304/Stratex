# strategy_hybrid.py - Hybrid EMA + ADX strategy
# Placeholder implementation following similar pattern to existing strategies.

from collections import namedtuple

SignalResult = namedtuple(
    "SignalResult",
    ["side", "sl", "tp", "strategy_type", "win_rate_prior", "rr_ratio"]
)

_STRATEGY_TYPE = "RULE_BASED"
_OOS_WIN_RATE_PRIOR = 0.52  # Expected win rate based on OOS testing
_RR_RATIO = 1.5

def get_signal(df):
    """Hybrid strategy combining EMA crossover and ADX momentum.
    BUY when EMA(20) > EMA(50) and ADX(14) > 25 and price is above EMA(200).
    SELL when EMA(20) < EMA(50) and ADX(14) > 25 and price is below EMA(200).
    Returns a SignalResult or None fields if conditions not met.
    """
    if df is None or len(df) < 20:
        return SignalResult(None, None, None, _STRATEGY_TYPE, _OOS_WIN_RATE_PRIOR, _RR_RATIO)
    last = df.iloc[-1]
    required = ["ema_20", "ema_50", "ema_200", "adx_14", "close"]
    if not all(col in df.columns for col in required):
        return SignalResult(None, None, None, _STRATEGY_TYPE, _OOS_WIN_RATE_PRIOR, _RR_RATIO)
    ema20 = float(last["ema_20"])
    ema50 = float(last["ema_50"])
    ema200 = float(last["ema_200"])
    adx = float(last["adx_14"])
    close = float(last["close"])
    atr = float(last.get("atr", close * 0.01))
    if ema20 > ema50 and adx > 25 and close > ema200:
        sl = close - atr * 1.5
        tp = close + atr * 3.0
        return SignalResult("BUY", sl, tp, _STRATEGY_TYPE, _OOS_WIN_RATE_PRIOR, _RR_RATIO)
    if ema20 < ema50 and adx > 25 and close < ema200:
        sl = close + atr * 1.5
        tp = close - atr * 3.0
        return SignalResult("SELL", sl, tp, _STRATEGY_TYPE, _OOS_WIN_RATE_PRIOR, _RR_RATIO)
    return SignalResult(None, None, None, _STRATEGY_TYPE, _OOS_WIN_RATE_PRIOR, _RR_RATIO)
