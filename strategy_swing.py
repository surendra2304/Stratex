# ==============================================================================
# STRATEGY_SWING.PY - Swing Trading via MACD + 200 EMA Trend Alignment
# ==============================================================================

from collections import namedtuple

SignalResult = namedtuple(
    "SignalResult",
    ["side", "sl", "tp", "strategy_type", "win_rate_prior", "rr_ratio"]
)

_STRATEGY_TYPE = "RULE_BASED"
_OOS_WIN_RATE_PRIOR = 0.45  # Swing MACD typically ~45% win rate
_RR_RATIO = 1.5             # 3 ATR / 2 ATR

def get_signal(df):
    """
    Swing Strategy:
    - BUY when price is above 200 EMA AND MACD crosses above signal line
    - SELL when price is below 200 EMA AND MACD crosses below signal line
    Returns: SignalResult
    """
    if df is None or len(df) < 2:
        return SignalResult(None, None, None, _STRATEGY_TYPE, _OOS_WIN_RATE_PRIOR, _RR_RATIO)

    last = df.iloc[-1]
    prev = df.iloc[-2]

    # Required columns check
    if 'macd' not in df.columns or 'ema_200' not in df.columns:
        return SignalResult(None, None, None, _STRATEGY_TYPE, _OOS_WIN_RATE_PRIOR, _RR_RATIO)

    close     = last["close"]
    ema_200   = last["ema_200"]
    atr       = last.get("atr", last.get("atr_14", 0.0))
    macd_now  = last["macd"]
    sig_now   = last["macd_signal"]
    macd_prev = prev["macd"]
    sig_prev  = prev["macd_signal"]
    rel_vol   = last.get("rel_volume", 1.0)

    # MACD crossover detection
    crossed_up   = macd_prev < sig_prev and macd_now > sig_now
    crossed_down = macd_prev > sig_prev and macd_now < sig_now

    # BUY: Above 200 EMA AND MACD just crossed up WITH above-average volume
    if close > ema_200 and crossed_up and rel_vol > 1.0 and macd_now < 0:
        sl = close - (atr * 2.0)
        tp = close + (atr * 3.0)
        return SignalResult("BUY", sl, tp, _STRATEGY_TYPE, _OOS_WIN_RATE_PRIOR, _RR_RATIO)

    # SELL: Below 200 EMA AND MACD just crossed down WITH above-average volume
    if close < ema_200 and crossed_down and rel_vol > 1.0 and macd_now > 0:
        sl = close + (atr * 2.0)
        tp = close - (atr * 3.0)
        return SignalResult("SELL", sl, tp, _STRATEGY_TYPE, _OOS_WIN_RATE_PRIOR, _RR_RATIO)

    return SignalResult(None, None, None, _STRATEGY_TYPE, _OOS_WIN_RATE_PRIOR, _RR_RATIO)

