# quantum/validation/bootstrap.py
"""Paired and independent bootstrap resampling with 10,000 samples for rigorous statistical hypothesis testing."""

import numpy as np
from dataclasses import dataclass
from typing import List, Dict, Any, Tuple

@dataclass
class BootstrapResult:
    comparison_name: str
    sample_size_method_a: int
    sample_size_method_b: int
    mean_difference: float
    ci_95_lower: float
    ci_95_upper: float
    p_value: float
    is_statistically_significant: bool
    is_entirely_positive: bool
    iterations: int = 10000
    random_seed: int = 42

def run_paired_bootstrap(
    returns_quantum: List[float],
    returns_classical: List[float],
    comparison_name: str = "Quantum_vs_Classical",
    n_iterations: int = 10000,
    seed: int = 42
) -> BootstrapResult:
    """
    Computes 10,000 bootstrap resamples on the difference between quantum and classical return series.
    Calculates empirical 95% two-sided confidence interval [2.5th percentile, 97.5th percentile].
    """
    rng = np.random.default_rng(seed)
    
    arr_q = np.array(returns_quantum)
    arr_c = np.array(returns_classical)
    
    len_q = len(arr_q)
    len_c = len(arr_c)
    
    if len_q == 0 or len_c == 0:
        return BootstrapResult(
            comparison_name=comparison_name,
            sample_size_method_a=len_q,
            sample_size_method_b=len_c,
            mean_difference=0.0,
            ci_95_lower=0.0,
            ci_95_upper=0.0,
            p_value=1.0,
            is_statistically_significant=False,
            is_entirely_positive=False,
            iterations=n_iterations,
            random_seed=seed
        )

    # If samples are of equal length, use paired bootstrap
    is_paired = (len_q == len_c)
    diff_means = np.zeros(n_iterations, dtype=np.float64)
    
    if is_paired:
        differences = arr_q - arr_c
        n = len(differences)
        for i in range(n_iterations):
            idx = rng.choice(n, size=n, replace=True)
            diff_means[i] = np.mean(differences[idx])
    else:
        for i in range(n_iterations):
            idx_q = rng.choice(len_q, size=len_q, replace=True)
            idx_c = rng.choice(len_c, size=len_c, replace=True)
            diff_means[i] = np.mean(arr_q[idx_q]) - np.mean(arr_c[idx_c])
            
    mean_diff = float(np.mean(diff_means))
    ci_lower = float(np.percentile(diff_means, 2.5))
    ci_upper = float(np.percentile(diff_means, 97.5))
    
    # Two-sided empirical p-value (proportion of bootstrap samples where diff <= 0)
    p_val = float(np.mean(diff_means <= 0) if mean_diff > 0 else np.mean(diff_means >= 0))
    p_val = min(1.0, p_val * 2.0)
    
    is_entirely_positive = (ci_lower > 0.0)
    is_sig = (p_val < 0.05) and (ci_lower > 0.0 or ci_upper < 0.0)
    
    return BootstrapResult(
        comparison_name=comparison_name,
        sample_size_method_a=len_q,
        sample_size_method_b=len_c,
        mean_difference=round(mean_diff, 4),
        ci_95_lower=round(ci_lower, 4),
        ci_95_upper=round(ci_upper, 4),
        p_value=round(p_val, 4),
        is_statistically_significant=is_sig,
        is_entirely_positive=is_entirely_positive,
        iterations=n_iterations,
        random_seed=seed
    )
