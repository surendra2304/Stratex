# quantum/validation/__init__.py
"""Quantum Validation Framework for Quantitative and Hybrid Strategy Benchmarking."""

from .data import validate_dataset, load_benchmark_data, DatasetAuditResult
from .splits import generate_walk_forward_splits, WalkForwardFold
from .baselines import ClassicalRuleBasedStrategy, ClassicalMLStrategy
from .quantum_models import QuantumVQCModel, HybridQuantumClassifier, QuantumPortfolioOptimizer
from .backtest import BacktestRunner, TradeRecord, BacktestResult
from .metrics import calculate_performance_metrics, PerformanceMetrics
from .bootstrap import run_paired_bootstrap, BootstrapResult
from .benchmark import run_full_benchmark, BenchmarkRunResult
from .report import generate_markdown_report

__all__ = [
    "validate_dataset",
    "load_benchmark_data",
    "DatasetAuditResult",
    "generate_walk_forward_splits",
    "WalkForwardFold",
    "ClassicalRuleBasedStrategy",
    "ClassicalMLStrategy",
    "QuantumVQCModel",
    "HybridQuantumClassifier",
    "QuantumPortfolioOptimizer",
    "BacktestRunner",
    "TradeRecord",
    "BacktestResult",
    "calculate_performance_metrics",
    "PerformanceMetrics",
    "run_paired_bootstrap",
    "BootstrapResult",
    "run_full_benchmark",
    "BenchmarkRunResult",
    "generate_markdown_report",
]
