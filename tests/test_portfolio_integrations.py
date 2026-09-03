import numpy as np, pandas as pd
from stratex_more_integrations import *

def test_optimizer():
    r=pd.DataFrame(np.random.default_rng(7).normal(0,.01,(300,4)),columns=list('ABCD'))
    w=PortfolioOptimizer(PortfolioConstraints(max_weight=.5)).minimum_variance(r)
    assert np.isfinite(w).all() and abs(w.sum()-1)<1e-6 and (w<=.5+1e-8).all()

def test_factor_report():
    f=pd.Series(np.linspace(-1,1,200)); ret=f*.01+.001
    x=factor_report(f,ret); assert x['n']==200 and x['ic']>.9

def test_vol():
    r=pd.Series(np.random.default_rng(4).normal(0,.01,1200))
    x=VolatilityForecaster().forecast(r); assert x['forecast_vol']>=0

def test_performance_overlay():
    r=pd.Series([.01,-.005,.002,-.01,.004]*50); assert 'sharpe' in performance_summary(r)
    w,_=PortfolioRiskOverlay(.4,1).apply(pd.Series({'A':.7,'B':.5,'C':-.2})); assert w.abs().sum()<=1+1e-9
