import pandas as pd


def calculate_diagnostics(trades, equity_curve, initial_balance=10000.0):
    if not trades:
        return {}
        
    df = pd.DataFrame(trades)
    
    # 1. Cost Analysis
    gross_pnl = df['gross_pnl'].sum()
    total_fees = df['fees'].sum()
    total_slippage = df['slippage'].sum()
    net_pnl = df['net_pnl'].sum()
    
    # 2. Trade Distribution
    winners = df[df['net_pnl'] > 0]
    losers = df[df['net_pnl'] <= 0]
    
    avg_winner = winners['net_pnl'].mean() if len(winners) > 0 else 0
    avg_loser = losers['net_pnl'].mean() if len(losers) > 0 else 0
    
    win_loss_ratio = abs(avg_winner / avg_loser) if avg_loser != 0 else float('inf')
    
    holding_times = df['holding_time'].describe().to_dict()
    
    # R-Multiple Analysis
    avg_r = df['r_multiple'].mean()
    
    # 3. Regime Analysis
    regime_stats = {}
    if 'regime' in df.columns:
        for regime, group in df.groupby('regime'):
            wins = len(group[group['net_pnl'] > 0])
            total = len(group)
            pf_gross = group[group['gross_pnl'] > 0]['gross_pnl'].sum() / abs(group[group['gross_pnl'] <= 0]['gross_pnl'].sum()) if group[group['gross_pnl'] <= 0]['gross_pnl'].sum() != 0 else float('inf')
            
            regime_stats[regime] = {
                'trades': total,
                'win_rate': (wins / total) * 100,
                'net_pnl': group['net_pnl'].sum(),
                'profit_factor': pf_gross
            }
            
    # Volatility State Analysis
    vol_stats = {}
    if 'volatility_state' in df.columns:
        for vstate, group in df.groupby('volatility_state'):
            wins = len(group[group['net_pnl'] > 0])
            total = len(group)
            vol_stats[vstate] = {
                'trades': total,
                'win_rate': (wins / total) * 100,
                'net_pnl': group['net_pnl'].sum()
            }
            
    # 4. Confidence Buckets Analysis
    confidence_stats = {}
    if 'confidence' in df.columns and df['confidence'].notna().any():
        # Drop rows where confidence is None
        conf_df = df.dropna(subset=['confidence']).copy()
        if not conf_df.empty:
            bins = [0, 0.55, 0.60, 0.65, 0.70, 0.75, 0.80, 1.0]
            labels = ['<55%', '55-60%', '60-65%', '65-70%', '70-75%', '75-80%', '>80%']
            conf_df['bucket'] = pd.cut(conf_df['confidence'], bins=bins, labels=labels, right=False)
            
            for bucket, group in conf_df.groupby('bucket', observed=False):
                wins = len(group[group['net_pnl'] > 0])
                total = len(group)
                if total > 0:
                    pf_gross = group[group['gross_pnl'] > 0]['gross_pnl'].sum() / abs(group[group['gross_pnl'] <= 0]['gross_pnl'].sum()) if group[group['gross_pnl'] <= 0]['gross_pnl'].sum() != 0 else float('inf')
                    confidence_stats[bucket] = {
                        'trades': total,
                        'win_rate': (wins / total) * 100,
                        'net_pnl': group['net_pnl'].sum(),
                        'profit_factor': pf_gross,
                        'avg_r_multiple': group['r_multiple'].mean()
                    }
            
    return {
        'cost_analysis': {
            'gross_pnl': gross_pnl,
            'fees': total_fees,
            'slippage': total_slippage,
            'net_pnl': net_pnl,
            'net_edge_per_trade': net_pnl / len(trades)
        },
        'trade_distribution': {
            'total_trades': len(trades),
            'win_rate': (len(winners) / len(trades)) * 100,
            'avg_winner': avg_winner,
            'avg_loser': avg_loser,
            'win_loss_ratio': win_loss_ratio,
            'avg_r_multiple': avg_r,
            'holding_time_mins': holding_times
        },
        'regime_performance': regime_stats,
        'volatility_performance': vol_stats,
        'confidence_buckets': confidence_stats
    }
