def get_signal(df):
    """Placeholder fast 5m strategy: always BUY with 1% SL/TP."""
    if df.empty:
        return None
    close = df['close'].iloc[-1]
    sl = close * 0.99
    tp = close * 1.01
    return ("BUY", sl, tp)
