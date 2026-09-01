#!/usr/bin/env python3
"""
scripts/compare_ab_performance.py — Statistical Performance Comparison Engine for A/B Forward Testing.

Performs rigorous statistical evaluation between Arm A (Control) and Arm B (Treatment):
1. Key metric calculation: Total Return, Profit Factor, Max Drawdown, Sharpe, Sortino, Win Rate, Avg Duration.
2. Hypothesis Testing:
   - Two-sample T-test (difference in trade mean returns).
   - Mann-Whitney U test (non-parametric return distribution difference).
   - Bootstrap 95% Confidence Intervals for expected return per trade.
3. Generates Markdown report (ab_test_report.md) with summary tables, significance conclusions, and recommendations.
4. Optionally renders equity curve comparison chart using matplotlib.
"""

import argparse
import datetime
import json
import os
from typing import Any

import numpy as np
import pandas as pd
from scipy import stats

from metrics import calculate_metrics


def load_trade_ledger(ledger_path: str) -> list[dict[str, Any]]:
    """Loads closed trade records from JSONL ledger."""
    if not os.path.exists(ledger_path):
        return []
    records = []
    with open(ledger_path, "r", encoding="utf-8") as f:
        for line in f:
            l = line.strip()
            if not l:
                continue
            try:
                records.append(json.loads(l))
            except Exception:
                continue
    return records


def load_equity_curve(equity_path: str) -> pd.DataFrame:
    """Loads equity curve into DataFrame."""
    if not os.path.exists(equity_path):
        return pd.DataFrame(columns=["timestamp", "equity", "cash"])
    records = []
    with open(equity_path, "r", encoding="utf-8") as f:
        for line in f:
            l = line.strip()
            if not l:
                continue
            try:
                records.append(json.loads(l))
            except Exception:
                continue
    df = pd.DataFrame(records)
    if not df.empty and "timestamp" in df.columns:
        df["timestamp"] = pd.to_datetime(df["timestamp"])
    return df


def bootstrap_ci(data: list[float], n_bootstrap: int = 2000, ci: float = 0.95) -> tuple[float, float]:
    """Computes bootstrap confidence interval for the mean."""
    if len(data) < 5:
        mean_val = float(np.mean(data)) if data else 0.0
        return mean_val, mean_val

    arr = np.array(data)
    boot_means = []
    np.random.seed(42)
    for _ in range(n_bootstrap):
        sample = np.random.choice(arr, size=len(arr), replace=True)
        boot_means.append(np.mean(sample))

    alpha_low = ((1.0 - ci) / 2.0) * 100.0
    alpha_high = (1.0 - (1.0 - ci) / 2.0) * 100.0
    low = float(np.percentile(boot_means, alpha_low))
    high = float(np.percentile(boot_means, alpha_high))
    return round(low, 4), round(high, 4)


def compute_ab_comparison(
    ledger_control: str = "paper_trade_ledger_control.jsonl",
    ledger_treatment: str = "paper_trade_ledger_treatment.jsonl",
    equity_control: str = "paper_equity_curve_control.jsonl",
    equity_treatment: str = "paper_equity_curve_treatment.jsonl",
    initial_capital: float = 10000.0,
    min_trades_for_sig: int = 30
) -> dict[str, Any]:
    """
    Computes comparative metrics and hypothesis tests.
    """
    trades_a = load_trade_ledger(ledger_control)
    trades_b = load_trade_ledger(ledger_treatment)
    df_eq_a = load_equity_curve(equity_control)
    df_eq_b = load_equity_curve(equity_treatment)

    metrics_a = calculate_metrics(trades_a, df_eq_a, initial_balance=initial_capital)
    metrics_b = calculate_metrics(trades_b, df_eq_b, initial_balance=initial_capital)

    pnl_a = [float(t.get("net_pnl", 0.0)) for t in trades_a]
    pnl_b = [float(t.get("net_pnl", 0.0)) for t in trades_b]

    # Average trade duration
    dur_a = np.mean([float(t.get("hold_duration_sec", 0)) for t in trades_a]) / 3600.0 if trades_a else 0.0
    dur_b = np.mean([float(t.get("hold_duration_sec", 0)) for t in trades_b]) / 3600.0 if trades_b else 0.0

    # Statistical tests
    t_stat, t_pvalue = 0.0, 1.0
    u_stat, u_pvalue = 0.0, 1.0
    ci_a = (0.0, 0.0)
    ci_b = (0.0, 0.0)

    has_enough_trades = len(trades_a) >= min_trades_for_sig and len(trades_b) >= min_trades_for_sig

    if len(pnl_a) >= 2 and len(pnl_b) >= 2:
        try:
            t_res = stats.ttest_ind(pnl_b, pnl_a, equal_var=False)
            t_stat, t_pvalue = float(t_res.statistic), float(t_res.pvalue)
        except Exception:
            pass

        try:
            u_res = stats.mannwhitneyu(pnl_b, pnl_a, alternative="two-sided")
            u_stat, u_pvalue = float(u_res.statistic), float(u_res.pvalue)
        except Exception:
            pass

        ci_a = bootstrap_ci(pnl_a)
        ci_b = bootstrap_ci(pnl_b)

    # Determine recommendation
    stat_sig = has_enough_trades and (t_pvalue < 0.05 or u_pvalue < 0.05)
    mean_a = float(np.mean(pnl_a)) if pnl_a else 0.0
    mean_b = float(np.mean(pnl_b)) if pnl_b else 0.0

    if not has_enough_trades:
        recommendation = "INSUFFICIENT_SAMPLE: Continue forward A/B testing until minimum 30 trades per arm are completed."
        verdict = "INCONCLUSIVE"
    elif stat_sig and mean_b > mean_a and metrics_b["profit_factor"] >= 1.20:
        recommendation = "PROMOTE TO TESTNET: AI Advisory demonstrated statistically significant superior performance (p < 0.05)."
        verdict = "PROMOTE_TREATMENT"
    elif stat_sig and mean_b < mean_a:
        recommendation = "REJECT AI ADVISORY: Baseline Control significantly outperformed Treatment. Maintain static parameters."
        verdict = "MAINTAIN_CONTROL"
    else:
        recommendation = "NO STATISTICAL ADVANTAGE: Difference between AI Advisory and Baseline is not statistically significant (p >= 0.05)."
        verdict = "NO_DIFFERENCE"

    return {
        "generated_at": datetime.datetime.utcnow().isoformat() + "Z",
        "sample_size": {
            "control_trades": len(trades_a),
            "treatment_trades": len(trades_b),
            "min_required_trades": min_trades_for_sig,
            "has_enough_samples": has_enough_trades
        },
        "arm_a_control": {
            "name": "CONTROL (Baseline Static)",
            "total_trades": metrics_a["total_trades"],
            "win_rate_pct": round(metrics_a["win_rate"], 2),
            "profit_factor": round(float(metrics_a["profit_factor"]), 2) if metrics_a["profit_factor"] else 0.0,
            "net_pnl": round(metrics_a["net_pnl"], 2),
            "return_pct": round(metrics_a["return_pct"], 2),
            "max_drawdown_pct": round(metrics_a["max_dd_pct"], 2),
            "sharpe_ratio": round(metrics_a["sharpe"], 2),
            "sortino_ratio": round(metrics_a["sortino"], 2),
            "avg_holding_hours": round(dur_a, 2),
            "mean_pnl_per_trade": round(mean_a, 2),
            "bootstrap_95_ci": ci_a
        },
        "arm_b_treatment": {
            "name": "TREATMENT (AI-Advised Dynamic)",
            "total_trades": metrics_b["total_trades"],
            "win_rate_pct": round(metrics_b["win_rate"], 2),
            "profit_factor": round(float(metrics_b["profit_factor"]), 2) if metrics_b["profit_factor"] else 0.0,
            "net_pnl": round(metrics_b["net_pnl"], 2),
            "return_pct": round(metrics_b["return_pct"], 2),
            "max_drawdown_pct": round(metrics_b["max_dd_pct"], 2),
            "sharpe_ratio": round(metrics_b["sharpe"], 2),
            "sortino_ratio": round(metrics_b["sortino"], 2),
            "avg_holding_hours": round(dur_b, 2),
            "mean_pnl_per_trade": round(mean_b, 2),
            "bootstrap_95_ci": ci_b
        },
        "statistical_tests": {
            "welch_t_test": {
                "t_statistic": round(t_stat, 4),
                "p_value": round(t_pvalue, 5),
                "statistically_significant": bool(t_pvalue < 0.05 and has_enough_trades)
            },
            "mann_whitney_u_test": {
                "u_statistic": round(u_stat, 4),
                "p_value": round(u_pvalue, 5),
                "statistically_significant": bool(u_pvalue < 0.05 and has_enough_trades)
            }
        },
        "evaluation_summary": {
            "verdict": verdict,
            "recommendation": recommendation
        }
    }


def generate_markdown_report(data: dict[str, Any], output_path: str = "ab_test_report.md") -> str:
    """Renders comprehensive Markdown report document."""
    ctrl = data["arm_a_control"]
    treat = data["arm_b_treatment"]
    stats_data = data["statistical_tests"]
    eval_data = data["evaluation_summary"]
    samples = data["sample_size"]
    ge_symbol = ">="
    delta_symbol = "Delta"
    mu_b = "mu_B"
    mu_a = "mu_A"

    md = f"""# AI Advisory A/B Forward Validation Performance Report

**Generated:** `{data['generated_at']}`  
**Experiment ID:** `ab_ai_advisory_001`  
**Evaluation Status:** `{eval_data['verdict']}`

---

## 1. Executive Summary & Recommendation

> **RECOMMENDATION:**  
> **{eval_data['recommendation']}**

- **Sample Sufficiency:** {samples['control_trades']} Control Trades / {samples['treatment_trades']} Treatment Trades (Target: {ge_symbol} {samples['min_required_trades']})
- **Statistical Significance ($p < 0.05$):** `{"YES" if stats_data['welch_t_test']['statistically_significant'] else "NO"}`

---

## 2. Key Performance Metrics Comparison

| Metric | Arm A (Control: Baseline) | Arm B (Treatment: AI-Advised) | Delta ({delta_symbol} B - A) | Advantage |
| :--- | :--- | :--- | :--- | :--- |
| **Total Closed Trades** | {ctrl['total_trades']} | {treat['total_trades']} | {treat['total_trades'] - ctrl['total_trades']:+d} | - |
| **Net PnL ($)** | ${ctrl['net_pnl']:.2f} | ${treat['net_pnl']:.2f} | ${treat['net_pnl'] - ctrl['net_pnl']:+.2f} | {"Arm B (Treatment)" if treat['net_pnl'] > ctrl['net_pnl'] else "Arm A (Control)"} |
| **Total Return (%)** | {ctrl['return_pct']:.2f}% | {treat['return_pct']:.2f}% | {treat['return_pct'] - ctrl['return_pct']:+.2f}% | {"Arm B" if treat['return_pct'] > ctrl['return_pct'] else "Arm A"} |
| **Profit Factor** | {ctrl['profit_factor']:.2f} | {treat['profit_factor']:.2f} | {treat['profit_factor'] - ctrl['profit_factor']:+.2f} | {"Arm B" if treat['profit_factor'] > ctrl['profit_factor'] else "Arm A"} |
| **Win Rate (%)** | {ctrl['win_rate_pct']:.2f}% | {treat['win_rate_pct']:.2f}% | {treat['win_rate_pct'] - ctrl['win_rate_pct']:+.2f}% | {"Arm B" if treat['win_rate_pct'] > ctrl['win_rate_pct'] else "Arm A"} |
| **Max Drawdown (%)** | {ctrl['max_drawdown_pct']:.2f}% | {treat['max_drawdown_pct']:.2f}% | {treat['max_drawdown_pct'] - ctrl['max_drawdown_pct']:+.2f}% | {"Arm B (Lower DD)" if treat['max_drawdown_pct'] < ctrl['max_drawdown_pct'] else "Arm A"} |
| **Sharpe Ratio** | {ctrl['sharpe_ratio']:.2f} | {treat['sharpe_ratio']:.2f} | {treat['sharpe_ratio'] - ctrl['sharpe_ratio']:+.2f} | {"Arm B" if treat['sharpe_ratio'] > ctrl['sharpe_ratio'] else "Arm A"} |
| **Sortino Ratio** | {ctrl['sortino_ratio']:.2f} | {treat['sortino_ratio']:.2f} | {treat['sortino_ratio'] - ctrl['sortino_ratio']:+.2f} | {"Arm B" if treat['sortino_ratio'] > ctrl['sortino_ratio'] else "Arm A"} |
| **Avg Trade Duration** | {ctrl['avg_holding_hours']:.1f}h | {treat['avg_holding_hours']:.1f}h | {treat['avg_holding_hours'] - ctrl['avg_holding_hours']:+.1f}h | - |
| **Mean PnL / Trade** | ${ctrl['mean_pnl_per_trade']:.2f} | ${treat['mean_pnl_per_trade']:.2f} | ${treat['mean_pnl_per_trade'] - ctrl['mean_pnl_per_trade']:+.2f} | {"Arm B" if treat['mean_pnl_per_trade'] > ctrl['mean_pnl_per_trade'] else "Arm A"} |
| **Bootstrap 95% CI ($)** | [{ctrl['bootstrap_95_ci'][0]:.2f}, {ctrl['bootstrap_95_ci'][1]:.2f}] | [{treat['bootstrap_95_ci'][0]:.2f}, {treat['bootstrap_95_ci'][1]:.2f}] | - | - |

---

## 3. Formal Statistical Hypothesis Testing

### A. Welch's Two-Sample T-Test (Difference in Means)
- **Null Hypothesis (H0):** Mean trade return of Arm B equals Mean trade return of Arm A ({mu_b} = {mu_a}).
- **t-statistic:** `{stats_data['welch_t_test']['t_statistic']}`
- **p-value:** `{stats_data['welch_t_test']['p_value']}`
- **Conclusion:** `{"Reject H0 (Statistically Significant Difference)" if stats_data['welch_t_test']['statistically_significant'] else "Fail to Reject H0 (No Significant Difference)"}`

### B. Mann-Whitney U Test (Distribution Rank Sum)
- **Null Hypothesis (H0):** Trade return distributions of Arm A and Arm B are identical.
- **U-statistic:** `{stats_data['mann_whitney_u_test']['u_statistic']}`
- **p-value:** `{stats_data['mann_whitney_u_test']['p_value']}`
- **Conclusion:** `{"Reject H0 (Significant Distribution Shift)" if stats_data['mann_whitney_u_test']['statistically_significant'] else "Fail to Reject H0 (Distributions Consistent)"}`
"""
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(md)
    return md


def main():
    parser = argparse.ArgumentParser(description="A/B Performance Statistical Comparison Engine.")
    parser.add_argument("--control-ledger", default="paper_trade_ledger_control.jsonl")
    parser.add_argument("--treatment-ledger", default="paper_trade_ledger_treatment.jsonl")
    parser.add_argument("--control-equity", default="paper_equity_curve_control.jsonl")
    parser.add_argument("--treatment-equity", default="paper_equity_curve_treatment.jsonl")
    parser.add_argument("--output-report", default="ab_test_report.md")
    parser.add_argument("--output-json", default="ab_comparison_results.json")
    parser.add_argument("--plot", action="store_true", help="Generate matplotlib equity curve comparison chart")
    args = parser.parse_args()

    results = compute_ab_comparison(
        ledger_control=args.control_ledger,
        ledger_treatment=args.treatment_ledger,
        equity_control=args.control_equity,
        equity_treatment=args.treatment_equity
    )

    # Output Markdown
    generate_markdown_report(results, output_path=args.output_report)
    print(f"[REPORT] Markdown report generated: {args.output_report}")

    # Output JSON
    with open(args.output_json, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2)
    print(f"[SAVED] JSON results written: {args.output_json}")

    # Optional Plot
    if args.plot:
        try:
            import matplotlib.pyplot as plt
            df_a = load_equity_curve(args.control_equity)
            df_b = load_equity_curve(args.treatment_equity)
            if not df_a.empty and not df_b.empty:
                plt.figure(figsize=(10, 5))
                plt.plot(df_a["timestamp"], df_a["equity"], label="Arm A: Control (Baseline)", color="#3B82F6")
                plt.plot(df_b["timestamp"], df_b["equity"], label="Arm B: Treatment (AI Advised)", color="#10B981")
                plt.title("A/B Forward Validation: Equity Curve Comparison")
                plt.xlabel("Timestamp")
                plt.ylabel("Equity (USDT)")
                plt.legend()
                plt.grid(True, alpha=0.3)
                plot_file = "ab_equity_comparison.png"
                plt.savefig(plot_file, dpi=150, bbox_inches="tight")
                print(f"[PLOT] Chart saved: {plot_file}")
        except Exception as e:
            print(f"[PLOT_WARN] Could not render plot: {e}")


if __name__ == "__main__":
    main()
