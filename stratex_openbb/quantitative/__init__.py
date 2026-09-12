"""
stratex_openbb/quantitative — OpenBB-grade quantitative risk, volatility, and distribution analytics.
"""

from .risk_metrics import (
    calculate_returns,
    calculate_sharpe_ratio,
    calculate_sortino_ratio,
    calculate_calmar_ratio,
    calculate_max_drawdown,
    calculate_var_historical,
    calculate_var_parametric,
    calculate_cvar,
    calculate_beta,
    compute_quantitative_risk_report,
)
from .volatility_estimators import (
    compute_parkinson_volatility,
    compute_garman_klass_volatility,
    compute_yang_zhang_volatility,
    compute_volatility_estimates_from_df,
)
from .distribution import analyze_return_distribution

__all__ = [
    "calculate_returns",
    "calculate_sharpe_ratio",
    "calculate_sortino_ratio",
    "calculate_calmar_ratio",
    "calculate_max_drawdown",
    "calculate_var_historical",
    "calculate_var_parametric",
    "calculate_cvar",
    "calculate_beta",
    "compute_quantitative_risk_report",
    "compute_parkinson_volatility",
    "compute_garman_klass_volatility",
    "compute_yang_zhang_volatility",
    "compute_volatility_estimates_from_df",
    "analyze_return_distribution",
]
