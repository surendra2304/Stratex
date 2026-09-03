from __future__ import annotations
import numpy as np
import pandas as pd

class PortfolioRiskOverlay:
    def __init__(self, max_single_weight: float = 0.35, max_gross: float = 1.0):
        self.max_single_weight, self.max_gross = max_single_weight, max_gross

    def apply(self, weights: pd.Series, corr: pd.DataFrame | None = None):
        w_series = pd.Series(weights, dtype=float)
        if not np.isfinite(w_series.to_numpy()).all():
            raise ValueError('non-finite portfolio allocation')
        w = w_series.copy()
        reasons = []
        if (w.abs() > self.max_single_weight).any():
            reasons.append('single_asset_cap')
            w = w.clip(-self.max_single_weight, self.max_single_weight)
        gross = float(w.abs().sum())
        if gross > self.max_gross and gross > 0:
            reasons.append('gross_exposure_cap')
            w *= self.max_gross / gross
        if corr is not None and not corr.empty:
            common = [a for a in w.index if a in corr.index]
            if len(common) >= 2:
                sub = corr.loc[common, common].abs()
                avg = (sub.sum(axis=1) - 1) / max(len(common) - 1, 1)
                w.loc[common] *= 1 / (1 + avg)
                reasons.append('correlation_penalty')
                gross = float(w.abs().sum())
                if gross > self.max_gross and gross > 0:
                    w *= self.max_gross / gross
        return w, reasons

