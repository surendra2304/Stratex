# Part 5: Timeframe Specific Target Constraints
# These are predefined based on generalized expected volatility 
# over different horizons to avoid post-test optimization.

TIMEFRAME_CONFIGS = {
    '1m': {
        'pt_pct': 0.003,    # 0.3% Take Profit
        'sl_pct': 0.0015,   # 0.15% Stop Loss
        'time_limit': 15    # 15 candles (15 mins)
    },
    '5m': {
        'pt_pct': 0.005,    # 0.5% Take Profit
        'sl_pct': 0.0025,   # 0.25% Stop Loss
        'time_limit': 12    # 12 candles (1 hour)
    },
    '15m': {
        'pt_pct': 0.010,    # 1.0% Take Profit
        'sl_pct': 0.005,    # 0.5% Stop Loss
        'time_limit': 16    # 16 candles (4 hours)
    },
    '30m': {
        'pt_pct': 0.015,    # 1.5% Take Profit
        'sl_pct': 0.0075,   # 0.75% Stop Loss
        'time_limit': 16    # 16 candles (8 hours)
    },
    '1h': {
        'pt_pct': 0.025,    # 2.5% Take Profit
        'sl_pct': 0.0125,   # 1.25% Stop Loss
        'time_limit': 24    # 24 candles (24 hours)
    },
    '4h': {
        'pt_pct': 0.060,    # 6.0% Take Profit
        'sl_pct': 0.030,    # 3.0% Stop Loss
        'time_limit': 18    # 18 candles (72 hours)
    }
}
