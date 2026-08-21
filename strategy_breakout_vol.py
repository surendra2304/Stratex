# strategy_breakout_vol.py - Volume Spike Breakout
# Placeholder implementation following existing pattern.

from collections import namedtuple

SignalResult = namedtuple(
    "SignalResult",
    ["side", "sl", "tp", "strategy_type", "win_rate_prior", "rr_ratio"]
)

_STRATEGY_TYPE = "RULE_BASED"
_OOS_WIN_RATE_PRIOR = 0.50
_RR_RATIO = 1.6

def get_signal(df):
    """Breakout strategy based on volume spikes.
    - BUY when volume > 2x average volume and price breaks above recent high.
    - SELL when volume > 2x average volume and price breaks below recent low.
    Returns a SignalResult.
    """
    if df is None or len(df) < 20:
        return SignalResult(None, None, None, _STRATEGY_TYPE, _OOS_WIN_RATE_PRIOR, _RR_RATIO)
    last = df.iloc[-1]
    required = ["close", "high", "low", "volume"]
    if not all(col in df.columns for col in required):
        return SignalResult(None, None, None, _STRATEGY_TYPE, _OOS_WIN_RATE_PRIOR, _RR_RATIO)
    close = float(last["close"])
    high = float(last["high"])
    low = float(last["low"])
    vol = float(last["volume"])
    avg_vol = float(df["volume"].tail(20).mean())
    atr = float(last.get("atr", close * 0.01))
    recent_high = float(df["high"].tail(20).max())
    recent_low = float(df["low"].tail(20).min())
    if vol > 2 * avg_vol and close > recent_high:
        sl = close - atr * 1.5
        tp = close + atr * 3.0
        return SignalResult("BUY", sl, tp, _STRATEGY_TYPE, _OOS_WIN_RATE_PRIOR, _RR_RATIO)
    if vol > 2 * avg_vol and close < recent_low:
        sl = close + atr * 1.5
        tp = close - atr * 3.0
        return SignalResult("SELL", sl, tp, _STRATEGY_TYPE, _OOS_WIN_RATE_PRIOR, _RR_RATIO)
    return SignalResult(None, None, None, _STRATEGY_TYPE, _OOS_WIN_RATE_PRIOR, _RR_RATIO)
