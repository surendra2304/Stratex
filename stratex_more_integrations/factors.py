from __future__ import annotations
import pandas as pd

def information_coefficient(factor: pd.Series, forward_returns: pd.Series, rank: bool = False) -> float:
    df = pd.concat([factor.rename('factor'), forward_returns.rename('return')], axis=1).dropna()
    if len(df) < 3: return float('nan')
    left = df['factor'].rank() if rank else df['factor']
    return float(left.corr(df['return']))

def factor_quantile_table(factor: pd.Series, forward_returns: pd.Series, q: int = 5) -> pd.DataFrame:
    df = pd.concat([factor.rename('factor'), forward_returns.rename('forward_return')], axis=1).dropna()
    if df.empty: return pd.DataFrame(columns=['count','mean','median','std'])
    df['quantile'] = pd.qcut(df['factor'].rank(method='first'), q=q, labels=False) + 1
    return df.groupby('quantile')['forward_return'].agg(['count','mean','median','std'])

def factor_report(factor: pd.Series, forward_returns: pd.Series, q: int = 5) -> dict:
    qt = factor_quantile_table(factor, forward_returns, q=q)
    spread = float('nan')
    if not qt.empty and q in qt.index and 1 in qt.index: spread = float(qt.loc[q,'mean'] - qt.loc[1,'mean'])
    return {'n': int(pd.concat([factor, forward_returns], axis=1).dropna().shape[0]),
            'ic': information_coefficient(factor, forward_returns),
            'rank_ic': information_coefficient(factor, forward_returns, rank=True),
            'quantile_return_spread': spread,
            'quantiles': qt.to_dict(orient='index')}
