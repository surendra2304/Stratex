#!/usr/bin/env python
"""scripts/run_quantum_benchmark.py
Executes the full 5-fold walk-forward benchmark across all 5 strategies,
runs 10,000 bootstrap resamplings, and writes QUANTUM_BENCHMARK_REPORT.md.
"""

import os
import sys
import datetime

# Ensure project root is in sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from quantum.validation import run_full_benchmark, generate_markdown_report

def main():
    print("=" * 80)
    print("STARTING QUANTUM RESEARCH PROFITABILITY BENCHMARK (PHASE 6 REMEDIATION)")
    print("=" * 80)
    
    symbol = "BTCUSDT"
    timeframe = "15m"
    n_folds = 5
    
    print(f"Target Asset: {symbol} | Base Timeframe: {timeframe} | Walk-Forward Folds: {n_folds}")
    print("Running walk-forward cross-validation engine...")
    
    result = run_full_benchmark(symbol=symbol, timeframe=timeframe, n_folds=n_folds, allow_proportional_fallback=True)
    
    print(f"Benchmark completed in {result.execution_time_sec:.2f} seconds.")
    print(f"Dataset Rows: {result.dataset_audit.rows} | Calendar Span: {result.dataset_audit.span_days} days")
    print(f"Folds Evaluated: {result.n_folds}")
    print(f"Quantum Advantage Verdict: {result.quantum_verdict}")
    print("-" * 80)
    
    print("AGGREGATE RESULTS:")
    for strat, m in result.aggregate_results.items():
        print(f"  {strat:30s} | Trades: {m['total_trades']:3d} | WinRate: {m['win_rate_pct']:5.1f}% | NetPnL: ${m['net_profit']:8.2f} | MaxDD: {m['max_drawdown_pct']:5.1f}%")
        
    print("-" * 80)
    print("BOOTSTRAP 10,000 STATISTICAL COMPARISONS:")
    for comp, b in result.bootstrap_results.items():
        print(f"  {comp:30s} | Diff: {b.mean_difference:+.4f}% | 95% CI: [{b.ci_95_lower:+.4f}%, {b.ci_95_upper:+.4f}%] | p-val: {b.p_value:.4f}")
        
    print("=" * 80)
    
    # Generate QUANTUM_BENCHMARK_REPORT.md
    report_content = generate_markdown_report(result)
    report_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "QUANTUM_BENCHMARK_REPORT.md"))
    with open(report_path, "w", encoding="utf-8") as f:
        f.write(report_content)
        
    print(f"Generated Benchmark Report at: {report_path}")
    print("=" * 80)
    
    if result.n_folds == 0:
        sys.exit(1)
    sys.exit(0)

if __name__ == "__main__":
    main()
