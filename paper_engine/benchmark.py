import pandas as pd
import numpy as np
from typing import Dict

class BenchmarkComparators:
    """
    Computes standard benchmark metrics to compare paper trading results against:
    1. Buy and Hold (B&H)
    2. Zero-Trade (starting capital only)
    3. Random Entry (Monte Carlo median)
    """
    
    @staticmethod
    def buy_and_hold(df: pd.DataFrame, starting_capital: float) -> Dict[str, float]:
        if df.empty:
            return {"net_pnl": 0.0, "return_pct": 0.0}
            
        start_price = df['close'].iloc[0]
        end_price = df['close'].iloc[-1]
        
        qty = starting_capital / start_price
        end_value = qty * end_price
        
        return {
            "net_pnl": end_value - starting_capital,
            "return_pct": (end_value - starting_capital) / starting_capital * 100
        }
        
    @staticmethod
    def random_entry_monte_carlo(df: pd.DataFrame, starting_capital: float, n_trades: int = 10, iterations: int = 1000) -> Dict[str, float]:
        """
        Simulates random long/short entries with fixed hold times.
        Returns the median and 5th percentile PnL.
        """
        if df.empty or len(df) < 10:
            return {"median_pnl": 0.0, "p05_pnl": 0.0}
            
        returns = df['close'].pct_change().dropna().values
        pnls = []
        
        for _ in range(iterations):
            idx = np.random.randint(0, len(returns), size=n_trades)
            dirs = np.random.choice([1, -1], size=n_trades)
            trade_rets = returns[idx] * dirs
            # approx cost
            trade_rets -= 0.002
            total_ret = np.sum(trade_rets)
            pnls.append(starting_capital * total_ret)
            
        return {
            "median_pnl": float(np.median(pnls)),
            "p05_pnl": float(np.percentile(pnls, 5))
        }
