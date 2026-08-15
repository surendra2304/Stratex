# ==============================================================================
# STRATEGY_SUPERTREND.PY - Maximum Profitability Trend Rider
# ==============================================================================

def get_signal(df):
    """
    Supertrend + 200 EMA Strategy:
    - BUY when Supertrend turns bullish AND price is above 200 EMA
    - SELL when Supertrend turns bearish AND price is below 200 EMA
    - Close signals are handled by the execution engine, but we will provide
      a dynamic SL that matches the supertrend band.
    """
    if df is None or len(df) < 200:
        return None, None, None

    last = df.iloc[-1]
    prev = df.iloc[-2]

    # Required columns from features.py: 'supertrend', 'ema_200', 'st_lower', 'st_upper'
    if 'supertrend' not in df.columns:
        return None, None, None

    st_now = last['supertrend']
    st_prev = prev['supertrend']
    close = last['close']
    ema = last['ema_200']

    # Trend changed to BULLISH
    if st_now == True and st_prev == False and close > ema:
        # Initial Stop Loss at the lower band
        sl = last['st_lower']
        # We want to ride the trend indefinitely. Set TP very high (e.g. 50% away)
        # Actually, trailing stop will take us out when supertrend flips.
        tp = close * 1.50
        return "BUY", sl, tp

    # Trend changed to BEARISH
    if st_now == False and st_prev == True and close < ema:
        # Initial Stop Loss at the upper band
        sl = last['st_upper']
        # Set TP very low (e.g. 50% away)
        tp = close * 0.50
        return "SELL", sl, tp

    return None, None, None
