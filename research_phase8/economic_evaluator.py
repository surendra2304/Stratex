import pandas as pd
import numpy as np

def calculate_net_expectancy(win_rate, pt_pct, sl_pct, fee_rate, slippage_rate):
    """
    Part 6: Strict Economic Evaluator
    Calculates the exact Net Expectancy of a trading edge, 
    incorporating round-trip fees and slippage on both Entry and Exit.
    """
    round_trip_cost = (fee_rate * 2) + (slippage_rate * 2)
    
    # Net outcomes
    net_win = pt_pct - round_trip_cost
    net_loss = -sl_pct - round_trip_cost # Both are losses, but friction is always subtracted
    
    # Expectancy = (Win % * Net Win) + (Loss % * Net Loss)
    expectancy = (win_rate * net_win) + ((1 - win_rate) * net_loss)
    
    # Profit factor = Gross Net Winning / Abs(Gross Net Losing)
    # Be careful here, if win rate is 0 or 1.
    gross_wins = win_rate * net_win
    gross_losses = abs((1 - win_rate) * net_loss)
    
    pf = gross_wins / (gross_losses + 1e-9)
    if net_win <= 0:
        pf = 0.0 # If friction is larger than PT, profit factor is 0
        
    return {
        "round_trip_cost": round_trip_cost,
        "net_win_pct": net_win,
        "net_loss_pct": net_loss,
        "net_expectancy": expectancy,
        "profit_factor": pf,
        "viable": expectancy > 0 and net_win > 0
    }

def calculate_timeframe_economics(tf_configs, fee_rate, slippage_rate):
    """
    Reports the cost as a % of expected gross move for each timeframe.
    """
    res = {}
    for tf, cfg in tf_configs.items():
        rt_cost = (fee_rate * 2) + (slippage_rate * 2)
        gross_move = cfg['pt_pct']
        cost_ratio = rt_cost / gross_move
        res[tf] = {
            "gross_pt": gross_move,
            "round_trip_cost": rt_cost,
            "cost_as_pct_of_move": cost_ratio
        }
    return res
