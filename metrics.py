import numpy as np
import pandas as pd


def calculate_drawdown(equity_df):
    """Calculates peak, drawdown, and max drawdown from an equity curve DataFrame."""
    if equity_df.empty:
        return 0, 0
    
    equity_df = equity_df.copy()
    equity_df['peak'] = equity_df['equity'].cummax()
    equity_df['drawdown'] = equity_df['peak'] - equity_df['equity']
    
    # Avoid div by zero
    equity_df['drawdown_pct'] = np.where(equity_df['peak'] > 0, equity_df['drawdown'] / equity_df['peak'], 0)
    
    max_dd = equity_df['drawdown'].max()
    max_dd_pct = equity_df['drawdown_pct'].max() * 100
    return max_dd, max_dd_pct

def calculate_metrics(trade_history, equity_df, initial_balance):
    if not trade_history:
        return {
            "initial_balance": initial_balance,
            "final_balance": initial_balance,
            "net_pnl": 0.0,
            "return_pct": 0.0,
            "total_trades": 0,
            "win_rate": 0.0,
            "profit_factor": 0.0,
            "expectancy": 0.0,
            "max_dd_pct": 0.0,
            "sharpe": 0.0,
            "sortino": 0.0,
            "calmar": 0.0,
            "largest_win": 0.0,
            "largest_loss": 0.0,
            "average_r": 0.0,
            "avg_holding_time": 0.0
        }
        
    trades = pd.DataFrame(trade_history)
    
    final_balance = initial_balance + trades['net_pnl'].sum()
    net_pnl = final_balance - initial_balance
    return_pct = (net_pnl / initial_balance) * 100 if initial_balance > 0 else 0.0
    
    total_trades = len(trades)
    wins = trades[trades['net_pnl'] > 0]
    losses = trades[trades['net_pnl'] <= 0]
    
    win_rate = (len(wins) / total_trades) * 100 if total_trades > 0 else 0.0
    
    gross_profit = wins['net_pnl'].sum() if not wins.empty else 0.0
    gross_loss = abs(losses['net_pnl'].sum()) if not losses.empty else 0.0
    
    if gross_loss > 0:
        profit_factor = gross_profit / gross_loss
    elif gross_profit > 0:
        profit_factor = float('inf')
    else:
        profit_factor = 0.0
        
    expectancy = trades['net_pnl'].mean() if not trades.empty else 0.0
    
    _max_dd, max_dd_pct = calculate_drawdown(equity_df)
    
    # Calculate Sharpe / Sortino 
    if not equity_df.empty:
        equity_df = equity_df.set_index('timestamp')
        # ffill handles missing values properly
        daily_equity = equity_df['equity'].resample('1D').last().ffill()
        daily_returns = daily_equity.pct_change().dropna()
        
        if len(daily_returns) > 1 and daily_returns.std() > 0:
            sharpe = (daily_returns.mean() / daily_returns.std()) * np.sqrt(365)
            downside = daily_returns[daily_returns < 0]
            if not downside.empty and downside.std() > 0:
                sortino = (daily_returns.mean() / downside.std()) * np.sqrt(365)
            else:
                sortino = float('inf') if daily_returns.mean() > 0 else 0.0
        else:
            sharpe = 0.0
            sortino = 0.0
            
        if max_dd_pct > 0:
            calmar = (daily_returns.mean() * 365) / (max_dd_pct / 100)
        else:
            calmar = float('inf') if daily_returns.mean() > 0 else 0.0
    else:
        sharpe = 0.0
        sortino = 0.0
        calmar = 0.0
        
    avg_r = trades['r_multiple'].mean() if 'r_multiple' in trades.columns else 0.0
        
    return {
        "initial_balance": initial_balance,
        "final_balance": final_balance,
        "net_pnl": net_pnl,
        "return_pct": return_pct,
        "total_trades": total_trades,
        "win_rate": win_rate,
        "profit_factor": profit_factor,
        "expectancy": expectancy,
        "max_dd_pct": max_dd_pct,
        "sharpe": sharpe,
        "sortino": sortino,
        "calmar": calmar,
        "largest_win": wins['net_pnl'].max() if not wins.empty else 0.0,
        "largest_loss": losses['net_pnl'].min() if not losses.empty else 0.0,
        "average_r": avg_r,
        "avg_holding_time": trades['holding_time'].mean() if not trades.empty else 0.0
    }
