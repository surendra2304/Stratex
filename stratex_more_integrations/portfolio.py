from __future__ import annotations
from dataclasses import dataclass
import numpy as np
import pandas as pd

@dataclass(frozen=True)
class PortfolioConstraints:
    max_weight: float = 0.35
    min_weight: float = 0.0
    target_gross: float = 1.0
    max_turnover: float | None = None

class PortfolioOptimizer:
    def __init__(self, constraints: PortfolioConstraints | None = None):
        self.constraints = constraints or PortfolioConstraints()

    def minimum_variance(self, returns: pd.DataFrame, previous: pd.Series | None = None) -> pd.Series:
        x = returns.dropna(how='all').dropna(axis=1, how='all')
        if x.shape[1] == 0:
            return pd.Series(dtype=float)
        cov = x.cov().fillna(0.0).to_numpy()
        assets = list(x.columns)
        try:
            import cvxpy as cp
            n = len(assets); w = cp.Variable(n)
            cons = [cp.sum(w) == self.constraints.target_gross,
                    w >= self.constraints.min_weight, w <= self.constraints.max_weight]
            if previous is not None and self.constraints.max_turnover is not None:
                p = previous.reindex(assets).fillna(0.0).to_numpy()
                cons.append(cp.norm1(w - p) <= self.constraints.max_turnover)
            prob = cp.Problem(cp.Minimize(cp.quad_form(w, cp.psd_wrap(cov))), cons)
            prob.solve(solver='CLARABEL', warm_start=True)
            if prob.status not in {'optimal', 'optimal_inaccurate'} or w.value is None:
                raise RuntimeError(str(prob.status))
            out = pd.Series(np.asarray(w.value).ravel(), index=assets)
        except Exception:
            vol = x.std(ddof=1).replace(0, np.nan).fillna(1.0)
            inv = 1.0 / vol.clip(lower=1e-12)
            out = inv / inv.sum() * self.constraints.target_gross
            out = out.clip(self.constraints.min_weight, self.constraints.max_weight)
            if out.sum() > 0: out = out / out.sum() * self.constraints.target_gross
        return out.sort_index()

    def risk_parity(self, returns: pd.DataFrame) -> pd.Series:
        x = returns.dropna(axis=1, how='all')
        vol = x.std(ddof=1).replace(0, np.nan).fillna(1.0)
        inv = 1.0 / vol.clip(lower=1e-12)
        w = inv / inv.sum() * self.constraints.target_gross
        w = w.clip(self.constraints.min_weight, self.constraints.max_weight)
        return (w / w.sum() * self.constraints.target_gross).sort_index()
