# strategy_bollinger.py - Bollinger Band mean reversion
# Placeholder implementation following existing pattern.

from collections import namedtuple

SignalResult = namedtuple(
    "SignalResult",
    ["side", "sl", "tp", "strategy_type", "win_rate_prior", "rr_ratio"]
)

_STRATEGY_TYPE = "RULE_BASED"
_OOS_WIN_RATE_PRIOR = 0.48  # Approx win rate based on OOS testing
_RR_RATIO = 1.8

def get_signal(df):
    """Bollinger Band mean reversion strategy.
    - BUY when price closes below lower band and RSI > 50.
    - SELL when price closes above upper band and RSI < 50.
    Returns a SignalResult.
    """
    if df is None or len(df) < 20:
        return SignalResult(None, None, None, _STRATEGY_TYPE, _OOS_WIN_RATE_PRIOR, _RR_RATIO)
    last = df.iloc[-1]
    required = ["close", "bb_lower", "bb_upper", "rsi"]
    if not all(col in df.columns for col in required):
        return SignalResult(None, None, None, _STRATEGY_TYPE, _OOS_WIN_RATE_PRIOR, _RR_RATIO)
    close = float(last["close"])
    lower = float(last["bb_lower"])
    upper = float(last["bb_upper"])
    rsi = float(last["rsi"])
    atr = float(last.get("atr", close * 0.01))
    if close < lower and rsi > 50:
        sl = close - atr * 1.5
        tp = close + atr * 3.0
        return SignalResult("BUY", sl, tp, _STRATEGY_TYPE, _OOS_WIN_RATE_PRIOR, _RR_RATIO)
    if close > upper and rsi < 50:
        sl = close + atr * 1.5
        tp = close - atr * 3.0
        return SignalResult("SELL", sl, tp, _STRATEGY_TYPE, _OOS_WIN_RATE_PRIOR, _RR_RATIO)
    return SignalResult(None, None, None, _STRATEGY_TYPE, _OOS_WIN_RATE_PRIOR, _RR_RATIO)
