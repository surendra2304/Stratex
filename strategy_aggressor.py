# ==============================================================================
# STRATEGY_AGGRESSOR.PY - High-Frequency Volume Delta Scalper
# ==============================================================================

def get_signal(df):
    """
    The Aggressor Strategy:
    - Analyzes Order Book Imbalance (Volume Delta)
    - BUY when extreme buying pressure + momentum trending up (RSI > 55)
    - SELL when extreme selling pressure + momentum trending down (RSI < 45)
    - Uses ultra-tight stop losses for high frequency
    """
    if df is None or len(df) < 2:
        return None, None, None

    last = df.iloc[-1]
    
    rsi = last["rsi"]
    close = last["close"]
    atr = last["atr"]
    vol_delta = last["vol_delta"]
    
    # Calculate average volume delta to find extremes
    avg_vol = df["volume"].tail(20).mean()
    extreme_vol_threshold = avg_vol * 0.5  # Delta must be 50% of avg volume
    
    # BUY: Massive buy volume delta AND RSI confirms upward momentum
    if vol_delta > extreme_vol_threshold and rsi > 55:
        sl = close - (atr * 1.5)  # Give a bit more breathing room
        tp = close + (atr * 4.0)  # Aim for higher R:R
        return "BUY", sl, tp

    # SELL: Massive sell volume delta AND RSI confirms downward momentum
    if vol_delta < -extreme_vol_threshold and rsi < 45:
        sl = close + (atr * 1.5)
        tp = close - (atr * 4.0)
        return "SELL", sl, tp

    return None, None, None
