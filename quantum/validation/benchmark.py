# quantum/validation/benchmark.py
"""Master Walk-Forward Quantum vs Classical Benchmark Orchestrator."""

import time
import pandas as pd
import numpy as np
from dataclasses import dataclass
from typing import Dict, List, Any, Optional

from .data import load_benchmark_data, DatasetAuditResult
from .splits import generate_walk_forward_splits, WalkForwardFold
from .baselines import ClassicalRuleBasedStrategy, ClassicalMLStrategy
from .quantum_models import QuantumVQCModel, HybridQuantumClassifier, QuantumPortfolioOptimizer
from .backtest import BacktestRunner, BacktestResult
from .bootstrap import run_paired_bootstrap, BootstrapResult

@dataclass
class BenchmarkRunResult:
    dataset_audit: DatasetAuditResult
    n_folds: int
    folds: List[WalkForwardFold]
    fold_results: Dict[int, Dict[str, BacktestResult]]
    aggregate_results: Dict[str, Dict[str, Any]]
    bootstrap_results: Dict[str, BootstrapResult]
    quantum_verdict: str
    verdict_rationale: str
    execution_time_sec: float

def run_full_benchmark(
    symbol: str = "BTCUSDT",
    timeframe: str = "15m",
    n_folds: int = 5,
    allow_proportional_fallback: bool = True
) -> BenchmarkRunResult:
    start_time = time.time()
    
    # 1. Load Data & Audit
    df, audit = load_benchmark_data(symbol=symbol, preferred_tf=timeframe)
    if df.empty:
        return BenchmarkRunResult(
            dataset_audit=audit,
            n_folds=0,
            folds=[],
            fold_results={},
            aggregate_results={},
            bootstrap_results={},
            quantum_verdict="C — INSUFFICIENT EVIDENCE",
            verdict_rationale="No valid market dataset could be loaded from data_cache.",
            execution_time_sec=time.time() - start_time
        )

    # 2. Build Chronological Splits
    folds, split_err = generate_walk_forward_splits(
        df, n_folds=n_folds, train_days=60.0, val_days=15.0, test_days=15.0,
        allow_proportional_fallback=allow_proportional_fallback
    )
    
    if not folds:
        return BenchmarkRunResult(
            dataset_audit=audit,
            n_folds=0,
            folds=[],
            fold_results={},
            aggregate_results={},
            bootstrap_results={},
            quantum_verdict="C — INSUFFICIENT EVIDENCE",
            verdict_rationale=f"Failed to generate walk-forward folds: {split_err}",
            execution_time_sec=time.time() - start_time
        )

    runner = BacktestRunner(initial_capital=10000.0, fee_rate=0.001, slippage_rate=0.0005)
    
    fold_results: Dict[int, Dict[str, BacktestResult]] = {}
    strategy_returns: Dict[str, List[float]] = {
        "Classical_Rule_Based": [],
        "Classical_ML_Baseline": [],
        "Pure_Quantum_VQC": [],
        "Hybrid_Quantum_Classical": [],
        "Quantum_Portfolio_Optimizer": []
    }
    
    for f in folds:
        k = f.fold_idx
        fold_results[k] = {}
        
        # Instantiate 5 models
        strat_rule = ClassicalRuleBasedStrategy()
        strat_ml = ClassicalMLStrategy()
        strat_vqc = QuantumVQCModel()
        strat_hybrid = HybridQuantumClassifier()
        strat_opt = QuantumPortfolioOptimizer()
        
        # Train on Fold Train Set
        strat_rule.fit(f.train_df)
        strat_ml.fit(f.train_df)
        strat_vqc.fit(f.train_df)
        strat_hybrid.fit(f.train_df)
        
        # Evaluate out-of-sample on Fold Test Set
        res_rule = runner.run_strategy(strat_rule, f.test_df, fold_idx=k)
        res_ml = runner.run_strategy(strat_ml, f.test_df, fold_idx=k)
        res_vqc = runner.run_strategy(strat_vqc, f.test_df, fold_idx=k)
        res_hybrid = runner.run_strategy(strat_hybrid, f.test_df, fold_idx=k)
        # Optimizer wrapper on Rule strategy
        res_opt = runner.run_strategy(strat_opt, f.test_df, fold_idx=k, is_optimizer_wrapper=True, base_strategy=strat_rule)
        
        fold_results[k]["Classical_Rule_Based"] = res_rule
        fold_results[k]["Classical_ML_Baseline"] = res_ml
        fold_results[k]["Pure_Quantum_VQC"] = res_vqc
        fold_results[k]["Hybrid_Quantum_Classical"] = res_hybrid
        fold_results[k]["Quantum_Portfolio_Optimizer"] = res_opt
        
        # Record trade-level net returns for bootstrap
        strategy_returns["Classical_Rule_Based"].extend([t.net_return_pct for t in res_rule.trades] if res_rule.trades else [0.0])
        strategy_returns["Classical_ML_Baseline"].extend([t.net_return_pct for t in res_ml.trades] if res_ml.trades else [0.0])
        strategy_returns["Pure_Quantum_VQC"].extend([t.net_return_pct for t in res_vqc.trades] if res_vqc.trades else [0.0])
        strategy_returns["Hybrid_Quantum_Classical"].extend([t.net_return_pct for t in res_hybrid.trades] if res_hybrid.trades else [0.0])
        strategy_returns["Quantum_Portfolio_Optimizer"].extend([t.net_return_pct for t in res_opt.trades] if res_opt.trades else [0.0])

    # 3. Aggregate Performance Across All Folds
    aggregate_results: Dict[str, Dict[str, Any]] = {}
    strat_keys = [
        "Classical_Rule_Based", "Classical_ML_Baseline", "Pure_Quantum_VQC",
        "Hybrid_Quantum_Classical", "Quantum_Portfolio_Optimizer"
    ]
    
    for s_name in strat_keys:
        all_trades = []
        for k in fold_results:
            all_trades.extend(fold_results[k][s_name].trades)
            
        total_trades = len(all_trades)
        wins = [t.net_pnl for t in all_trades if t.net_pnl > 0]
        losses = [t.net_pnl for t in all_trades if t.net_pnl <= 0]
        net_profit = sum(t.net_pnl for t in all_trades)
        total_fees = sum(t.fees for t in all_trades)
        total_slippage = sum(t.slippage for t in all_trades)
        
        win_rate = (len(wins) / total_trades * 100.0) if total_trades > 0 else 0.0
        profit_factor = (sum(wins) / abs(sum(losses))) if losses and abs(sum(losses)) > 0 else (99.0 if wins else 0.0)
        net_return_pct = (net_profit / (10000.0 * len(folds))) * 100.0 if folds else 0.0
        
        # Max drawdown across fold runs
        max_dds = [fold_results[k][s_name].max_drawdown_pct for k in fold_results]
        avg_dd = float(np.mean(max_dds)) if max_dds else 0.0
        
        # Average Latency
        lats = [fold_results[k][s_name].avg_latency_ms for k in fold_results]
        avg_lat = float(np.mean(lats)) if lats else 0.0
        
        backend_used = fold_results[1][s_name].backend_used if fold_results else "N/A"
        
        aggregate_results[s_name] = {
            "total_trades": total_trades,
            "wins": len(wins),
            "losses": len(losses),
            "win_rate_pct": round(win_rate, 2),
            "net_profit": round(net_profit, 2),
            "net_return_pct": round(net_return_pct, 2),
            "profit_factor": round(profit_factor, 2),
            "avg_trade_pnl": round(float(np.mean([t.net_pnl for t in all_trades])) if all_trades else 0.0, 2),
            "max_drawdown_pct": round(avg_dd, 2),
            "total_fees": round(total_fees, 2),
            "total_slippage": round(total_slippage, 2),
            "turnover": round(sum(t.entry_price * 10.0 for t in all_trades), 2),
            "avg_latency_ms": round(avg_lat, 2),
            "backend_used": backend_used
        }

    # 4. Run 10,000 Bootstrap Resamplings
    boot_vqc = run_paired_bootstrap(strategy_returns["Pure_Quantum_VQC"], strategy_returns["Classical_Rule_Based"], "Pure_VQC_vs_Classical_Rule")
    boot_hybrid = run_paired_bootstrap(strategy_returns["Hybrid_Quantum_Classical"], strategy_returns["Classical_ML_Baseline"], "Hybrid_vs_Classical_ML")
    boot_opt = run_paired_bootstrap(strategy_returns["Quantum_Portfolio_Optimizer"], strategy_returns["Classical_Rule_Based"], "Optimizer_vs_Classical_Rule")
    
    bootstrap_results = {
        "Pure_VQC_vs_Classical_Rule": boot_vqc,
        "Hybrid_vs_Classical_ML": boot_hybrid,
        "Optimizer_vs_Classical_Rule": boot_opt
    }

    # 5. Classify Quantum Advantage
    # Check Decision Rules
    if not audit.is_sufficient_for_90d_wf:
        verdict = "C — INSUFFICIENT EVIDENCE"
        rationale = f"Historical data span ({audit.span_days} days) is less than the strict 90-day calendar requirement for full 60d/15d/15d splits. Evaluated on proportional rolling partitions."
    elif boot_vqc.is_entirely_positive or boot_hybrid.is_entirely_positive:
        verdict = "A — QUANTUM ADVANTAGE DETECTED"
        rationale = "Quantum model produced a statistically significant positive net return difference where the 95% bootstrap confidence interval is strictly positive on out-of-sample test folds."
    else:
        verdict = "B — NO QUANTUM ADVANTAGE DETECTED"
        rationale = f"Walk-forward out-of-sample benchmark completed across {len(folds)} folds. Neither Pure VQC nor Hybrid models produced an entirely positive 95% confidence interval over classical baselines after fees and slippage."

    return BenchmarkRunResult(
        dataset_audit=audit,
        n_folds=len(folds),
        folds=folds,
        fold_results=fold_results,
        aggregate_results=aggregate_results,
        bootstrap_results=bootstrap_results,
        quantum_verdict=verdict,
        verdict_rationale=rationale,
        execution_time_sec=time.time() - start_time
    )
