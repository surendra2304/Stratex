from __future__ import annotations
import numpy as np
import pandas as pd

def performance_summary(returns: pd.Series, periods_per_year: int = 365) -> dict:
    r = pd.Series(returns).dropna().astype(float)
    if r.empty: return {}
    equity = (1+r).cumprod(); dd = equity/equity.cummax()-1
    vol = float(r.std(ddof=1)*np.sqrt(periods_per_year)) if len(r)>1 else 0.0
    ann = float(r.mean()*periods_per_year); sharpe = ann/vol if vol>0 else float('nan')
    dvol = float(r[r<0].std(ddof=1)*np.sqrt(periods_per_year)) if len(r[r<0])>1 else float('nan')
    sortino = ann/dvol if np.isfinite(dvol) and dvol>0 else float('nan')
    years = len(r)/periods_per_year; cagr = float(equity.iloc[-1]**(1/years)-1) if years>0 else float('nan')
    mdd = float(dd.min())
    return {'total_return':float(equity.iloc[-1]-1),'cagr':cagr,'annualized_volatility':vol,
            'sharpe':sharpe,'sortino':sortino,'max_drawdown':mdd,
            'calmar':cagr/abs(mdd) if mdd<0 else float('nan'),'win_rate':float((r>0).mean()),'periods':int(len(r))}

def drawdown_series(returns: pd.Series) -> pd.Series:
    r = pd.Series(returns).fillna(0.0); e = (1+r).cumprod(); return e/e.cummax()-1
