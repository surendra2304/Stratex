# quantum/validation/__init__.py
"""Quantum Validation Framework for Quantitative and Hybrid Strategy Benchmarking."""

from .backtest import BacktestResult, BacktestRunner, TradeRecord
from .baselines import ClassicalMLStrategy, ClassicalRuleBasedStrategy
from .benchmark import BenchmarkRunResult, run_full_benchmark
from .bootstrap import BootstrapResult, run_paired_bootstrap
from .data import DatasetAuditResult, load_benchmark_data, validate_dataset
from .metrics import PerformanceMetrics, calculate_performance_metrics
from .quantum_models import (
    HybridQuantumClassifier,
    QuantumPortfolioOptimizer,
    QuantumVQCModel,
)
from .report import generate_markdown_report
from .splits import WalkForwardFold, generate_walk_forward_splits

__all__ = [
    "BacktestResult",
    "BacktestRunner",
    "BenchmarkRunResult",
    "BootstrapResult",
    "ClassicalMLStrategy",
    "ClassicalRuleBasedStrategy",
    "DatasetAuditResult",
    "HybridQuantumClassifier",
    "PerformanceMetrics",
    "QuantumPortfolioOptimizer",
    "QuantumVQCModel",
    "TradeRecord",
    "WalkForwardFold",
    "calculate_performance_metrics",
    "generate_markdown_report",
    "generate_walk_forward_splits",
    "load_benchmark_data",
    "run_full_benchmark",
    "run_paired_bootstrap",
    "validate_dataset",
]
