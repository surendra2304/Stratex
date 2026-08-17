def get_signal(df):
    """Generate a simple BUY signal on every candle.
    This placeholder strategy is used for high‑frequency testing.
    It returns a BUY side with a 1% stop‑loss and 1% take‑profit
    based on the latest close price.
    """
    if df.empty:
        return None
    close = df['close'].iloc[-1]
    sl = close * 0.99  # 1% below
    tp = close * 1.01  # 1% above
    return {
        "side": "BUY",
        "sl": sl,
        "tp": tp,
        "confidence": 0.5,
        "prob_win": 0.6,
        "expected_gross_return": tp - close,
        "expected_net_return": (tp - close) * 0.99,
    }
