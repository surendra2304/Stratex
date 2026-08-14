# ==============================================================================
# STRATEGY_SCALPER.PY - High-Frequency Mean Reversion: RSI + Bollinger Bands
# ==============================================================================

def get_signal(df):
    """
    Scalping Strategy:
    - BUY when price touches the lower Bollinger Band AND RSI is oversold (<30)
    - SELL when price touches the upper Bollinger Band AND RSI is overbought (>70)
    Returns: "BUY", "SELL", or None
    """
    if df is None or df.empty or len(df) < 2:
        return None, None, None

    last = df.iloc[-1]
    prev = df.iloc[-2]

    rsi = last["rsi"]
    close = last["close"]
    bb_upper = last["bb_upper"]
    bb_lower = last["bb_lower"]
    atr = last["atr"]

    # BUY: Price wicked below lower band and RSI is oversold
    if close <= bb_lower and rsi < 35:
        sl = close - (atr * 1.5)
        tp = close + (atr * 1.0)
        return "BUY", sl, tp

    # SELL: Price wicked above upper band and RSI is overbought
    if close >= bb_upper and rsi > 65:
        sl = close + (atr * 1.5)
        tp = close - (atr * 1.0)
        return "SELL", sl, tp

    return None, None, None
