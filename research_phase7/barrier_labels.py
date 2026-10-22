import numpy as np


def apply_triple_barrier_labels(df, pt_pct, sl_pct, time_limit):
    """
    Part 15 & 16: Triple-Barrier Labeling Framework.
    Evaluates UPPER BARRIER (Take Profit), LOWER BARRIER (Stop Loss), 
    and TIME BARRIER (Timeout).
    Returns specific state classifications and time-to-barrier.
    """
    labels = []
    time_to_barrier = []
    
    closes = df['close'].values
    highs = df['high'].values
    lows = df['low'].values
    
    n = len(closes)
    
    for i in range(n):
        if i + time_limit >= n:
            labels.append(np.nan)
            time_to_barrier.append(np.nan)
            continue
            
        entry_price = closes[i]
        upper_barrier = entry_price * (1 + pt_pct)
        lower_barrier = entry_price * (1 - sl_pct)
        
        # We assume direction is symmetric, but we want to know what it hit first.
        hit_type = "TIMEOUT"
        bars_taken = time_limit
        
        # Look forward up to time_limit
        for j in range(1, time_limit + 1):
            idx = i + j
            if idx >= n:
                break
                
            curr_high = highs[idx]
            curr_low = lows[idx]
            
            # Did we hit UPPER?
            if curr_high >= upper_barrier and curr_low > lower_barrier:
                hit_type = "HIT_UPPER"
                bars_taken = j
                break
            # Did we hit LOWER?
            elif curr_low <= lower_barrier and curr_high < upper_barrier or curr_high >= upper_barrier and curr_low <= lower_barrier:
                hit_type = "HIT_LOWER"
                bars_taken = j
                break
                
        labels.append(hit_type)
        time_to_barrier.append(bars_taken)
        
    df = df.copy()
    df['barrier_hit'] = labels
    df['time_to_barrier'] = time_to_barrier
    
    # Map to ML labels depending on the perspective (Long or Short)
    # We will provide long perspective labels for simplicity:
    # LONG_WIN if it hit upper, LONG_LOSS if it hit lower
    df['long_label'] = np.where(df['barrier_hit'] == 'HIT_UPPER', 1, 0)
    df['short_label'] = np.where(df['barrier_hit'] == 'HIT_LOWER', 1, 0)
    
    return df
