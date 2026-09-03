from __future__ import annotations
import numpy as np
import pandas as pd

class VolatilityForecaster:
    def __init__(self, fallback_span: int = 48): self.fallback_span = fallback_span
    def forecast(self, returns: pd.Series, horizon: int = 1) -> dict:
        r = pd.Series(returns).dropna().astype(float)
        if len(r) < 50:
            vol = float(r.ewm(span=self.fallback_span).std().iloc[-1]) if len(r) else float('nan')
            return {'model':'ewm_fallback','forecast_vol':vol,'horizon':horizon,'fitted':False}
        try:
            from arch import arch_model
            fit = arch_model(r * 100.0, mean='Constant', vol='GARCH', p=1, q=1, dist='t').fit(disp='off')
            f = fit.forecast(horizon=horizon, reindex=False)
            var = float(f.variance.iloc[-1, -1]) / 10000.0
            return {'model':'GARCH(1,1)-t','forecast_variance':var,
                    'forecast_vol':float(np.sqrt(max(var,0.0))),'horizon':horizon,'fitted':True}
        except Exception as exc:
            vol = float(r.ewm(span=self.fallback_span).std().iloc[-1])
            return {'model':'ewm_fallback','forecast_vol':vol,'horizon':horizon,
                    'fitted':False,'fallback_reason':type(exc).__name__}
