# ==============================================================================
# STRATEGY_SWING.PY - Swing Trading via MACD + 200 EMA Trend Alignment
# ==============================================================================

def get_signal(df):
    """
    Swing Strategy:
    - BUY when price is above 200 EMA AND MACD crosses above signal line
    - SELL when price is below 200 EMA AND MACD crosses below signal line
    Returns: "BUY", "SELL", or None
    """
    if df is None or len(df) < 2:
        return None, None, None

    last = df.iloc[-1]
    prev = df.iloc[-2]

    close     = last["close"]
    ema_200   = last["ema_200"]
    atr       = last["atr"]
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
        return "BUY", sl, tp

    # SELL: Below 200 EMA AND MACD just crossed down WITH above-average volume
    if close < ema_200 and crossed_down and rel_vol > 1.0 and macd_now > 0:
        sl = close + (atr * 2.0)
        tp = close - (atr * 3.0)
        return "SELL", sl, tp

    return None, None, None
