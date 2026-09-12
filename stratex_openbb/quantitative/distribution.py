"""
stratex_openbb/quantitative/distribution.py — Return Distribution and Normality Tests.
Calculates skewness, kurtosis, Jarque-Bera normality test, and percentile statistics.
"""

from typing import Any, Dict, Sequence, Union
import numpy as np
from scipy import stats


def analyze_return_distribution(
    returns: Union[np.ndarray, Sequence[float]]
) -> Dict[str, Any]:
    """
    Analyzes empirical distribution properties of financial returns:
    - Mean, median, standard deviation
    - Skewness (asymmetry)
    - Excess kurtosis (fat tails)
    - Jarque-Bera normality test statistic and p-value
    """
    arr = np.asarray(returns, dtype=float)
    arr = arr[np.isfinite(arr)]
    if len(arr) < 8:
        return {
            "sample_size": len(arr),
            "mean": 0.0,
            "median": 0.0,
            "std": 0.0,
            "skewness": 0.0,
            "excess_kurtosis": 0.0,
            "jarque_bera_stat": 0.0,
            "is_normal": True,
            "p_value": 1.0
        }

    mean_val = float(np.mean(arr))
    med_val = float(np.median(arr))
    std_val = float(np.std(arr, ddof=1))
    skew_val = float(stats.skew(arr))
    kurt_val = float(stats.kurtosis(arr))  # Fisher's excess kurtosis (normal == 0.0)
    jb_stat, p_val = stats.jarque_bera(arr)

    return {
        "sample_size": len(arr),
        "mean": round(mean_val, 6),
        "median": round(med_val, 6),
        "std": round(std_val, 6),
        "skewness": round(skew_val, 4),
        "excess_kurtosis": round(kurt_val, 4),
        "jarque_bera_stat": round(float(jb_stat), 4),
        "p_value": round(float(p_val), 6),
        "is_normal": bool(p_val > 0.05)
    }
