import numpy as np
import pandas as pd

def run_monte_carlo(trades, initial_balance=10000.0, iterations=10000, ruin_level=0.5):
    """
    Part 23: Monte Carlo Trade Sequence Test.
    Resamples the OOS trades to estimate maximum drawdown and probability of ruin.
    """
    if not trades:
        return {}

    pnls = [t['net_pnl'] for t in trades]
    n_trades = len(pnls)
    
    max_drawdowns = []
    ruin_count = 0
    
    for _ in range(iterations):
        # Sample with replacement
        simulated_pnls = np.random.choice(pnls, size=n_trades, replace=True)
        equity_curve = initial_balance + np.cumsum(simulated_pnls)
        
        # Calculate max drawdown for this path
        running_max = np.maximum.accumulate(equity_curve)
        drawdowns = (running_max - equity_curve) / running_max
        max_dd = np.max(drawdowns)
        max_drawdowns.append(max_dd)
        
        # Check ruin (e.g. hitting 50% drawdown)
        if np.any(drawdowns >= ruin_level):
            ruin_count += 1
            
    avg_max_dd = np.mean(max_drawdowns)
    worst_max_dd = np.max(max_drawdowns)
    ruin_prob = ruin_count / iterations
    
    return {
        "iterations": iterations,
        "avg_max_drawdown": avg_max_dd * 100,
        "worst_max_drawdown": worst_max_dd * 100,
        "probability_of_ruin": ruin_prob * 100
    }
