"""
paper_engine/statistical_report.py

Explicit statistical validation for forward experiment results.

CRITICAL METHODOLOGY NOTES:
  - Passing software tests (pytest) proves RELIABILITY only.
  - Positive PnL in backtests proves HISTORICAL FIT only.
  - Positive PnL in historical held-out data proves SIMULATED OOS only.
  - Positive PnL in genuine wall-clock forward data is the MINIMUM for economic validation.
  - None of the above guarantees future profitability.

Every significance claim must include:
  - Hypothesis H0 and H1
  - Test name and statistic
  - Sample size
  - p-value
  - Confidence interval (where appropriate)
  - Multiple comparison correction (where multiple strategies/symbols tested)
  - Explicit INCONCLUSIVE label when sample size is too small
"""
import math
import time

import numpy as np

# Minimum trades required for any statistical inference
MIN_TRADES_FOR_INFERENCE = 30
# Minimum trades for Sharpe/Sortino to be meaningful
MIN_TRADES_FOR_SHARPE = 50


def compute_trade_stats(trade_returns: list[float]) -> dict:
    """
    Compute descriptive statistics for a list of per-trade returns (as fractions).
    Returns all metrics; marks unreliable ones with INSUFFICIENT_SAMPLE.
    """
    n = len(trade_returns)
    if n == 0:
        return {"error": "NO_TRADES", "sample_size": 0}

    arr = np.array(trade_returns)
    wins = arr[arr > 0]
    losses = arr[arr < 0]

    win_rate = len(wins) / n if n > 0 else 0.0
    gross_profit = float(wins.sum()) if len(wins) > 0 else 0.0
    gross_loss = float(abs(losses.sum())) if len(losses) > 0 else 0.0
    profit_factor = (gross_profit / gross_loss) if gross_loss > 0 else float("inf")
    expectancy = float(arr.mean())

    sharpe = None
    sharpe_note = "INSUFFICIENT_SAMPLE"
    sortino = None
    sortino_note = "INSUFFICIENT_SAMPLE"

    if n >= MIN_TRADES_FOR_SHARPE:
        std = float(arr.std(ddof=1))
        sharpe = float(arr.mean() / std) if std > 0 else 0.0
        sharpe_note = "OK"
        downside = arr[arr < 0]
        down_std = float(downside.std(ddof=1)) if len(downside) > 1 else 0.0
        sortino = float(arr.mean() / down_std) if down_std > 0 else 0.0
        sortino_note = "OK"

    return {
        "sample_size": n,
        "win_rate": round(win_rate, 4),
        "gross_profit_pct": round(gross_profit * 100, 4),
        "gross_loss_pct": round(gross_loss * 100, 4),
        "profit_factor": round(profit_factor, 4),
        "expectancy_pct": round(expectancy * 100, 6),
        "mean_return_pct": round(float(arr.mean()) * 100, 6),
        "std_return_pct": round(float(arr.std(ddof=1)) * 100, 6) if n > 1 else 0.0,
        "min_return_pct": round(float(arr.min()) * 100, 4),
        "max_return_pct": round(float(arr.max()) * 100, 4),
        "sharpe": round(sharpe, 4) if sharpe is not None else None,
        "sharpe_note": sharpe_note,
        "sortino": round(sortino, 4) if sortino is not None else None,
        "sortino_note": sortino_note,
    }


def t_test_positive_expectancy(trade_returns: list[float]) -> dict:
    """
    One-sample t-test: H0 = mean return <= 0, H1 = mean return > 0.

    Hypothesis
    ----------
    H0: E[r] <= 0  (strategy has non-positive expectancy after all costs)
    H1: E[r] > 0   (strategy has strictly positive expectancy after all costs)
    Test: one-sample, one-tailed t-test (scipy.stats.ttest_1samp)
    Observation unit: per-trade net return (after all fees, slippage, spread)
    """
    n = len(trade_returns)
    result = {
        "hypothesis_h0": "mean_return <= 0",
        "hypothesis_h1": "mean_return > 0",
        "test": "one_sample_t_test_one_tailed",
        "observation_unit": "per_trade_net_return_fraction",
        "sample_size": n,
        "min_sample_required": MIN_TRADES_FOR_INFERENCE,
    }

    if n < MIN_TRADES_FOR_INFERENCE:
        result.update({
            "verdict": "INCONCLUSIVE",
            "reason": f"Insufficient sample size: {n} < {MIN_TRADES_FOR_INFERENCE}",
            "p_value": None,
            "t_stat": None,
            "ci_95": None,
        })
        return result

    try:
        from scipy import stats
        arr = np.array(trade_returns)
        t_stat, p_two_tailed = stats.ttest_1samp(arr, 0.0)
        # One-tailed p-value for H1: mean > 0
        p_one_tailed = p_two_tailed / 2.0 if t_stat > 0 else 1.0

        # 95% CI via t-distribution
        se = float(arr.std(ddof=1)) / math.sqrt(n)
        t_crit = stats.t.ppf(0.975, df=n - 1)
        mean = float(arr.mean())
        ci_low = mean - t_crit * se
        ci_high = mean + t_crit * se

        result.update({
            "mean_return": round(mean, 8),
            "t_stat": round(float(t_stat), 6),
            "p_value_one_tailed": round(float(p_one_tailed), 6),
            "ci_95_low": round(ci_low, 8),
            "ci_95_high": round(ci_high, 8),
            "verdict": "PASS" if p_one_tailed < 0.05 else "INCONCLUSIVE",
            "note": (
                "PASS means p < 0.05 for H1: mean > 0. "
                "This does NOT guarantee future profitability."
            ),
        })
    except ImportError:
        result.update({
            "verdict": "INCONCLUSIVE",
            "reason": "scipy not available — install scipy for statistical testing",
            "p_value": None,
        })

    return result


def compute_max_drawdown(equity_curve: list[float]) -> float:
    """Compute maximum drawdown from a list of equity values."""
    if len(equity_curve) < 2:
        return 0.0
    peak = equity_curve[0]
    max_dd = 0.0
    for eq in equity_curve:
        peak = max(peak, eq)
        if peak > 0:
            dd = (peak - eq) / peak
            max_dd = max(max_dd, dd)
    return max_dd


def can_classify(
    config,
    n_closed_trades: int,
) -> dict:
    """
    CLASSIFICATION GATE — must be called before evaluate_against_acceptance_criteria.

    Classification is ONLY permitted when:
      1. Closed trades >= min_required_trades (default 30)

    Returns
    -------
    dict with:
      allowed       : bool
      verdict       : "ALLOWED" | "BLOCKED_TRADES"
      message       : human-readable explanation
      n_closed_trades: int
      trade_gate    : bool
    """
    trade_gate = n_closed_trades >= config.min_required_trades

    if trade_gate:
        verdict = "ALLOWED"
        message = (
            f"Trade gate satisfied: {n_closed_trades} trades >= {config.min_required_trades}. "
            "Proceed with full statistical evaluation."
        )
        allowed = True
    else:
        verdict = "BLOCKED_TRADES"
        remaining_trades = config.min_required_trades - n_closed_trades
        message = (
            f"CLASSIFICATION BLOCKED: "
            f"Need {remaining_trades} more trades (have {n_closed_trades}/{config.min_required_trades})."
        )
        allowed = False

    return {
        "allowed": allowed,
        "verdict": verdict,
        "message": message,
        "n_closed_trades": n_closed_trades,
        "trade_gate": trade_gate,
        "min_required_trades": config.min_required_trades,
    }


def evaluate_against_acceptance_criteria(
    config,  # FrozenExperimentConfig
    trade_returns: list[float],
    equity_curve: list[float],
    benchmark_result: dict,
) -> dict:
    """
    Evaluate forward experiment results against pre-registered acceptance criteria.

    CRITICAL: This function MUST NOT be called unless can_classify() returns allowed=True.
    Callers must enforce this.
    """
    n = len(trade_returns)

    # ── Full evaluation ───────────────────────────────────────────────────
    stats_result = compute_trade_stats(trade_returns)
    significance = t_test_positive_expectancy(trade_returns)
    max_dd = compute_max_drawdown(equity_curve)

    criteria = {}

    # 1. Minimum sample size (re-checked)
    criteria["min_trades"] = {
        "required": config.min_required_trades,
        "actual": n,
        "verdict": "PASS" if n >= config.min_required_trades else "INCONCLUSIVE",
    }

    # 2. Expectancy > 0
    exp = stats_result.get("expectancy_pct", None)
    if n < MIN_TRADES_FOR_INFERENCE:
        criteria["expectancy"] = {"verdict": "INCONCLUSIVE", "required": "> 0%", "actual": exp}
    else:
        criteria["expectancy"] = {
            "required": "> 0%",
            "actual": f"{exp:.4f}%" if exp is not None else "N/A",
            "verdict": "PASS" if exp is not None and exp > 0 else "FAIL",
        }

    # 3. Profit factor
    pf = stats_result.get("profit_factor", 0.0)
    if n < MIN_TRADES_FOR_INFERENCE:
        criteria["profit_factor"] = {
            "verdict": "INCONCLUSIVE",
            "required": f">= {config.required_profit_factor}",
        }
    else:
        criteria["profit_factor"] = {
            "required": f">= {config.required_profit_factor}",
            "actual": f"{pf:.4f}",
            "verdict": "PASS" if pf >= config.required_profit_factor else "FAIL",
        }

    # 4. Max drawdown
    criteria["max_drawdown"] = {
        "required": f"<= {config.max_acceptable_drawdown_pct * 100:.1f}%",
        "actual": f"{max_dd * 100:.2f}%",
        "verdict": "PASS" if max_dd <= config.max_acceptable_drawdown_pct else "FAIL",
    }

    # 5. Statistical significance
    criteria["statistical_significance"] = {
        "test": significance.get("test"),
        "p_value": significance.get("p_value_one_tailed"),
        "sample_size": n,
        "verdict": significance.get("verdict", "INCONCLUSIVE"),
        "note": (
            "p < 0.05 means we reject H0 (non-positive expectancy) at the 5% level. "
            "This is a necessary but NOT sufficient condition for deployment."
        ),
    }

    # 6. Beats random benchmark
    frac = benchmark_result.get("fraction_beating_strategy")
    if frac is not None:
        criteria["beats_random"] = {
            "fraction_of_sims_beating_strategy": frac,
            "verdict": (
                "PASS" if frac < 0.5
                else ("INCONCLUSIVE" if n < MIN_TRADES_FOR_INFERENCE else "FAIL")
            ),
            "note": "< 50% means strategy outperforms the median random entry.",
        }
    else:
        criteria["beats_random"] = {"verdict": "INCONCLUSIVE", "reason": "Benchmark not computed"}

    # Overall verdict
    verdicts = [v["verdict"] for v in criteria.values()]
    if "FAIL" in verdicts:
        overall = "FAIL"
    elif all(v == "PASS" for v in verdicts):
        overall = "PASS"
    else:
        overall = "INCONCLUSIVE"

    return {
        "overall_verdict": overall,
        "evaluated_at": time.time(),
        "n_closed_trades": n,
        "criteria": criteria,
        "statistics": stats_result,
        "significance_test": significance,
        "max_drawdown": round(max_dd, 6),
        "classification_rule": (
            "Final classification requires >= "
            f"{config.min_required_trades} closed trades."
        ),
        "disclaimer": (
            "PASS does not mean the system will be profitable in future. "
            "It means the forward experiment met pre-registered minimum criteria. "
            "Live deployment requires additional human review."
        ),
    }

