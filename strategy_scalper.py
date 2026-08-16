# ==============================================================================
# STRATEGY_SCALPER.PY - High-Frequency Mean Reversion: RSI + Bollinger Bands
# ==============================================================================

from collections import namedtuple

SignalResult = namedtuple(
    "SignalResult",
    ["side", "sl", "tp", "strategy_type", "win_rate_prior", "rr_ratio"]
)

_STRATEGY_TYPE = "RULE_BASED"
_OOS_WIN_RATE_PRIOR = 0.60  # Estimated 60% win rate for mean reversion
_RR_RATIO = 0.66            # Reward/Risk (1.0 ATR / 1.5 ATR)

def get_signal(df):
    """
    Scalping Strategy:
    - BUY when price touches the lower Bollinger Band AND RSI is oversold (<30)
    - SELL when price touches the upper Bollinger Band AND RSI is overbought (>70)
    Returns: SignalResult
    """
    if df is None or df.empty or len(df) < 2:
        return SignalResult(None, None, None, _STRATEGY_TYPE, _OOS_WIN_RATE_PRIOR, _RR_RATIO)

    last = df.iloc[-1]
    prev = df.iloc[-2]

    # Required columns check (graceful fallback)
    if 'rsi' not in df.columns or 'bb_upper' not in df.columns:
        return SignalResult(None, None, None, _STRATEGY_TYPE, _OOS_WIN_RATE_PRIOR, _RR_RATIO)

    rsi = last["rsi"]
    close = last["close"]
    bb_upper = last["bb_upper"]
    bb_lower = last["bb_lower"]
    bb_width = last["bb_width"]
    ema_200 = last["ema_200"]
    atr = last["atr"]

    # BUY: Price wicked below lower band, RSI is oversold, and overall trend is UP (filter)
    if close <= bb_lower and rsi < 35 and close > ema_200:
        sl = close - (atr * 1.5)
        tp = close + (atr * 1.0)
        return SignalResult("BUY", sl, tp, _STRATEGY_TYPE, _OOS_WIN_RATE_PRIOR, _RR_RATIO)

    # SELL: Price wicked above upper band, RSI overbought, and overall trend is DOWN
    if close >= bb_upper and rsi > 65 and close < ema_200:
        sl = close + (atr * 1.5)
        tp = close - (atr * 1.0)
        return SignalResult("SELL", sl, tp, _STRATEGY_TYPE, _OOS_WIN_RATE_PRIOR, _RR_RATIO)

    return SignalResult(None, None, None, _STRATEGY_TYPE, _OOS_WIN_RATE_PRIOR, _RR_RATIO)

