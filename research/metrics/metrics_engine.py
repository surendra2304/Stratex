import numpy as np
import pandas as pd


def calculate_drawdown(equity_df):
    if equity_df is None or equity_df.empty or 'equity' not in equity_df.columns:
        return 0.0, 0.0
    
    df = equity_df.copy()
    df['peak'] = df['equity'].cummax()
    df['drawdown'] = df['peak'] - df['equity']
    df['drawdown_pct'] = np.where(df['peak'] > 0, df['drawdown'] / df['peak'], 0.0)
    
    max_dd = float(df['drawdown'].max()) if not df['drawdown'].empty else 0.0
    max_dd_pct = float(df['drawdown_pct'].max() * 100) if not df['drawdown_pct'].empty else 0.0
    return max_dd, max_dd_pct


def calculate_metrics(trade_history, equity_df, initial_balance=10000.0):
    if not trade_history:
        return {
            'initial_balance': initial_balance,
            'final_balance': initial_balance,
            'net_pnl': 0.0,
            'return_pct': 0.0,
            'total_trades': 0,
            'winning_trades': 0,
            'losing_trades': 0,
            'win_rate': 0.0,
            'gross_profit': 0.0,
            'gross_loss': 0.0,
            'profit_factor': 0.0,
            'profit_factor_str': 'UNDEFINED',
            'expectancy': 0.0,
            'average_win': 0.0,
            'average_loss': 0.0,
            'max_dd_pct': 0.0,
            'sharpe': 0.0,
            'sortino': 0.0,
            'calmar': 0.0,
            'largest_win': 0.0,
            'largest_loss': 0.0,
            'average_r': 0.0,
            'avg_holding_time': 0.0,
            'total_fees': 0.0,
            'total_slippage': 0.0,
            'evidence_grade': 'GRADE D (below 30 trades)',
            'reliability_warning': 'Sample size is 0 trades. No statistical significance.'
        }
        
    trades = pd.DataFrame(trade_history)
    total_trades = len(trades)
    wins = trades[trades['net_pnl'] > 0]
    losses = trades[trades['net_pnl'] <= 0]
    
    win_count = len(wins)
    loss_count = len(losses)
    win_rate = (win_count / total_trades) * 100 if total_trades > 0 else 0.0
    
    net_pnl = float(trades['net_pnl'].sum())
    final_balance = initial_balance + net_pnl
    return_pct = (net_pnl / initial_balance) * 100 if initial_balance > 0 else 0.0
    
    gross_profit = float(wins['gross_pnl'].sum()) if ('gross_pnl' in wins.columns and not wins.empty) else (float(wins['net_pnl'].sum()) if not wins.empty else 0.0)
    gross_loss = abs(float(losses['gross_pnl'].sum())) if ('gross_pnl' in losses.columns and not losses.empty) else (abs(float(losses['net_pnl'].sum())) if not losses.empty else 0.0)
    
    if gross_loss > 0:
        pf_val = gross_profit / gross_loss
        pf_str = str(round(pf_val, 4))
    elif gross_profit > 0:
        pf_val = float('inf')
        pf_str = 'UNDEFINED (Zero Losses)'
    else:
        pf_val = 0.0
        pf_str = '0.0000'
        
    expectancy = float(trades['net_pnl'].mean()) if not trades.empty else 0.0
    avg_win = float(wins['net_pnl'].mean()) if not wins.empty else 0.0
    avg_loss = float(losses['net_pnl'].mean()) if not losses.empty else 0.0
    
    _max_dd, max_dd_pct = calculate_drawdown(equity_df)
    
    if equity_df is not None and not equity_df.empty and 'timestamp' in equity_df.columns:
        eq = equity_df.copy()
        if not pd.api.types.is_datetime64_any_dtype(eq['timestamp']):
            eq['timestamp'] = pd.to_datetime(eq['timestamp'])
        eq = eq.set_index('timestamp')
        daily_equity = eq['equity'].resample('1D').last().ffill()
        daily_returns = daily_equity.pct_change().dropna()
        
        if len(daily_returns) > 1 and daily_returns.std() > 0:
            sharpe = float((daily_returns.mean() / daily_returns.std()) * np.sqrt(365))
            downside = daily_returns[daily_returns < 0]
            if not downside.empty and downside.std() > 0:
                sortino = float((daily_returns.mean() / downside.std()) * np.sqrt(365))
            else:
                sortino = float('inf') if daily_returns.mean() > 0 else 0.0
        else:
            sharpe = 0.0
            sortino = 0.0
            
        if max_dd_pct > 0:
            calmar = float((daily_returns.mean() * 365) / (max_dd_pct / 100)) if len(daily_returns) > 0 else 0.0
        else:
            calmar = float('inf') if (len(daily_returns) > 0 and daily_returns.mean() > 0) else 0.0
    else:
        sharpe = 0.0
        sortino = 0.0
        calmar = 0.0
        
    avg_r = float(trades['r_multiple'].mean()) if 'r_multiple' in trades.columns else 0.0
    total_fees = float(trades['fees'].sum()) if 'fees' in trades.columns else (float(trades['entry_fee'].sum()) if 'entry_fee' in trades.columns else 0.0)
    total_slippage = float(trades['slippage'].sum()) if 'slippage' in trades.columns else 0.0
    
    if total_trades < 30:
        evidence_grade = 'GRADE D (under 30 trades)'
        warning = 'CRITICAL: Sample size is under 30 trades. Statistically insufficient.'
    elif total_trades < 100:
        evidence_grade = 'GRADE C (30-99 trades)'
        warning = 'Moderate sample size. Walk-forward validation required.'
    elif total_trades < 300:
        evidence_grade = 'GRADE B (100-299 trades)'
        warning = 'Adequate sample size. Verify across multiple market regimes.'
    else:
        evidence_grade = 'GRADE A (300+ trades)'
        warning = 'Statistically robust sample size.'
        
    return {
        'initial_balance': initial_balance,
        'final_balance': final_balance,
        'net_pnl': net_pnl,
        'return_pct': return_pct,
        'total_trades': total_trades,
        'winning_trades': win_count,
        'losing_trades': loss_count,
        'win_rate': win_rate,
        'gross_profit': gross_profit,
        'gross_loss': gross_loss,
        'profit_factor': pf_val,
        'profit_factor_str': pf_str,
        'expectancy': expectancy,
        'average_win': avg_win,
        'average_loss': avg_loss,
        'max_dd_pct': max_dd_pct,
        'sharpe': sharpe,
        'sortino': sortino,
        'calmar': calmar,
        'largest_win': float(wins['net_pnl'].max()) if not wins.empty else 0.0,
        'largest_loss': float(losses['net_pnl'].min()) if not losses.empty else 0.0,
        'average_r': avg_r,
        'avg_holding_time': float(trades['holding_time'].mean()) if not trades.empty and 'holding_time' in trades.columns else 0.0,
        'total_fees': total_fees,
        'total_slippage': total_slippage,
        'evidence_grade': evidence_grade,
        'reliability_warning': warning
    }
