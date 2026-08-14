import pandas as pd
import numpy as np

def run_market_making_baseline(df, maker_fee=0.0002, taker_fee=0.001, spread_capture=0.0002, inventory_limit=3):
    """
    Part 8 & 9: Market Making Economics
    Simulates a naive naive market maker constantly quoting the bid and ask.
    """
    # This is a high-level statistical evaluation.
    # We want to know: is the volatility of the asset so high that 
    # adverse selection destroys spread capture?
    
    # 1. Expected Spread Capture
    # If we assume we capture the spread `spread_capture` (e.g. 2 bps) 
    # minus the maker fee `maker_fee` on both sides:
    # Gross edge per round trip = spread_capture - (2 * maker_fee)
    
    # Example:
    # Capturing a 0.02% spread.
    # Paying 0.02% maker fee on entry, 0.02% maker fee on exit.
    # Net edge = 0.02% - 0.04% = -0.02%.
    
    # 2. Adverse Selection
    # If the price moves against us by more than the spread before we can unwind, we lose.
    # We measure this by looking at 1-minute candle high/low ranges.
    
    ranges = (df['high'] - df['low']) / df['open']
    avg_range = ranges.mean()
    
    # If avg 1-min candle range > 0.1%, then sitting on the bid/ask 
    # exposes us to massive adverse selection (we get run over by momentum).
    
    # We will simulate simply being filled on the Bid.
    # Does the price bounce back up to the Ask within N candles, or does it keep dropping?
    
    bounces = 0
    run_overs = 0
    
    for i in range(1, len(df) - 5):
        # We quote the bid.
        # Let's say we get filled if the close drops.
        if df['close'].iloc[i] < df['close'].iloc[i-1]:
            # We are LONG.
            entry = df['close'].iloc[i]
            target = entry * (1 + spread_capture)
            stop = entry * (1 - avg_range)
            
            # Look forward 5 candles
            for j in range(1, 6):
                future_high = df['high'].iloc[i+j]
                future_low = df['low'].iloc[i+j]
                
                if future_low <= stop:
                    run_overs += 1
                    break
                elif future_high >= target:
                    bounces += 1
                    break
                    
    total = bounces + run_overs
    if total == 0: total = 1
    
    win_rate = bounces / total
    
    # Net Expectancy of Market Making
    net_win = spread_capture - (maker_fee * 2)
    net_loss = -avg_range - (maker_fee + taker_fee) # Taker fee to stop out
    
    net_expectancy = (win_rate * net_win) + ((1 - win_rate) * net_loss)
    
    return {
        "avg_candle_range": avg_range,
        "spread_capture": spread_capture,
        "win_rate": win_rate,
        "net_expectancy": net_expectancy,
        "bounces": bounces,
        "run_overs": run_overs
    }
