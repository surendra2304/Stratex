from statsmodels.tsa.stattools import adfuller


def run_pairs_research(df_a, df_b, asset_a="BTCUSDT", asset_b="ETHUSDT"):
    """
    Part 12 & 13: Statistical Arbitrage / Relative Value Research
    Tests cointegration and mean-reversion of a synthetic spread.
    """
    # 1. Align data by timestamp
    df_a = df_a[['timestamp', 'close']].rename(columns={'close': 'close_a'}).set_index('timestamp')
    df_b = df_b[['timestamp', 'close']].rename(columns={'close': 'close_b'}).set_index('timestamp')
    
    df = df_a.join(df_b, how='inner').dropna()
    
    if len(df) < 500:
        return {"status": "UNAVAILABLE", "reason": "Insufficient overlapping data"}
        
    # 2. Calculate Beta (Hedge Ratio) via simple linear regression or ratio
    # For a naive statistical arbitrage baseline, we just use the price ratio
    df['spread'] = df['close_a'] / df['close_b']
    
    # 3. Stationarity Test (ADF)
    adf_result = adfuller(df['spread'])
    p_value = adf_result[1]
    is_stationary = p_value < 0.05
    
    # 4. Z-Score
    spread_mean = df['spread'].rolling(window=100).mean()
    spread_std = df['spread'].rolling(window=100).std()
    df['z_score'] = (df['spread'] - spread_mean) / (spread_std + 1e-9)
    
    # 5. Baseline Execution Simulation
    # Strategy: Short the spread when Z > 2 (Short A, Long B)
    # Long the spread when Z < -2 (Long A, Short B)
    # Exit when Z crosses 0
    
    in_trade = 0 # 1 for Long Spread, -1 for Short Spread
    entry_spread = 0
    trades = 0
    gross_pnl_pct = 0.0
    
    for i in range(100, len(df)):
        z = df['z_score'].iloc[i]
        curr_spread = df['spread'].iloc[i]
        
        if in_trade == 0:
            if z > 2.0:
                in_trade = -1
                entry_spread = curr_spread
            elif z < -2.0:
                in_trade = 1
                entry_spread = curr_spread
        elif in_trade == 1:
            if z >= 0:
                # Exit Long
                pnl = (curr_spread - entry_spread) / entry_spread
                gross_pnl_pct += pnl
                trades += 1
                in_trade = 0
        elif in_trade == -1 and z <= 0:
            # Exit Short
            pnl = (entry_spread - curr_spread) / entry_spread
            gross_pnl_pct += pnl
            trades += 1
            in_trade = 0
                
    # 6. Cost Evaluation
    # Since it's a pair, every trade involves 4 legs:
    # Enter A, Enter B, Exit A, Exit B
    # Round trip friction per trade = 0.15% (for single asset) * 2 = 0.30%
    round_trip_cost = 0.0030
    total_friction = trades * round_trip_cost
    net_pnl = gross_pnl_pct - total_friction
    
    return {
        "status": "AVAILABLE",
        "pair": f"{asset_a}/{asset_b}",
        "samples": len(df),
        "stationary": bool(is_stationary),
        "adf_p_value": float(p_value),
        "trades": trades,
        "gross_pnl_pct": float(gross_pnl_pct),
        "net_pnl_pct": float(net_pnl),
        "viable": net_pnl > 0
    }
