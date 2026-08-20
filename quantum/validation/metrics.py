# quantum/validation/metrics.py
"""Financial and performance metric calculations."""

import numpy as np
from dataclasses import dataclass
from typing import List, Any

@dataclass
class PerformanceMetrics:
    total_trades: int
    wins: int
    losses: int
    win_rate_pct: float
    gross_profit: float
    gross_loss: float
    profit_factor: float
    net_profit: float
    net_return_pct: float
    avg_trade_pnl: float
    median_trade_pnl: float
    max_drawdown_pct: float
    sharpe_ratio: float
    sortino_ratio: float
    total_fees: float
    total_slippage: float
    turnover: float

def calculate_performance_metrics(trades: List[Any], equity_curve: List[float], initial_capital: float = 10000.0) -> PerformanceMetrics:
    total_trades = len(trades)
    if total_trades == 0:
        return PerformanceMetrics(
            total_trades=0, wins=0, losses=0, win_rate_pct=0.0,
            gross_profit=0.0, gross_loss=0.0, profit_factor=0.0,
            net_profit=0.0, net_return_pct=0.0, avg_trade_pnl=0.0,
            median_trade_pnl=0.0, max_drawdown_pct=0.0,
            sharpe_ratio=0.0, sortino_ratio=0.0, total_fees=0.0,
            total_slippage=0.0, turnover=0.0
        )
        
    pnls = [t.net_pnl for t in trades]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]
    
    n_wins = len(wins)
    n_losses = len(losses)
    win_rate = (n_wins / total_trades) * 100.0
    
    gross_profit = float(sum(wins))
    gross_loss = float(abs(sum(losses)))
    profit_factor = (gross_profit / gross_loss) if gross_loss > 0 else (99.0 if gross_profit > 0 else 0.0)
    
    net_profit = float(sum(pnls))
    net_return_pct = (net_profit / initial_capital) * 100.0
    avg_trade = float(np.mean(pnls))
    median_trade = float(np.median(pnls))
    
    # Calculate Max Drawdown from equity curve
    eq = np.array(equity_curve)
    peaks = np.maximum.accumulate(eq)
    dds = (peaks - eq) / peaks * 100.0
    max_dd = float(np.max(dds)) if len(dds) > 0 else 0.0
    
    # Returns series for Sharpe and Sortino
    trade_returns = np.array([t.net_return_pct / 100.0 for t in trades])
    if len(trade_returns) > 1 and np.std(trade_returns) > 0:
        sharpe = float((np.mean(trade_returns) / np.std(trade_returns)) * np.sqrt(252))
        downside = trade_returns[trade_returns < 0]
        sortino = float((np.mean(trade_returns) / np.std(downside)) * np.sqrt(252)) if len(downside) > 0 and np.std(downside) > 0 else sharpe
    else:
        sharpe = 0.0
        sortino = 0.0
        
    total_fees = float(sum(t.fees for t in trades))
    total_slippage = float(sum(t.slippage for t in trades))
    turnover = float(sum(t.entry_price * (abs(t.net_pnl) + 10.0) for t in trades))
    
    return PerformanceMetrics(
        total_trades=total_trades,
        wins=n_wins,
        losses=n_losses,
        win_rate_pct=round(win_rate, 2),
        gross_profit=round(gross_profit, 2),
        gross_loss=round(gross_loss, 2),
        profit_factor=round(profit_factor, 2),
        net_profit=round(net_profit, 2),
        net_return_pct=round(net_return_pct, 2),
        avg_trade_pnl=round(avg_trade, 2),
        median_trade_pnl=round(median_trade, 2),
        max_drawdown_pct=round(max_dd, 2),
        sharpe_ratio=round(sharpe, 2),
        sortino_ratio=round(sortino, 2),
        total_fees=round(total_fees, 2),
        total_slippage=round(total_slippage, 2),
        turnover=round(turnover, 2)
    )
