"""
config_ab.py — Frozen Experiment Configuration for Parallel A/B Testing.

Compares:
  - Arm A (CONTROL): Baseline strategies with static config_strategy.py parameters.
  - Arm B (TREATMENT): Same strategies with AI-Universe advisory modifications dynamically applied via AdvisoryParameterOverlay.

CRITICAL INVARIANTS:
1. Identical initial conditions: starting equity, timestamp, market candles, and symbols.
2. Separate file namespaces for control and treatment.
3. Pre-registered statistical acceptance criteria (p < 0.05, min 30 trades per arm).
4. Automatic safety termination if either arm breaches max drawdown (default 10%).
"""

import json
import os
import time
from dataclasses import asdict, dataclass, field

from logger import get_logger

logger = get_logger("config_ab")

AB_EXPERIMENT_DIR = os.getenv("AB_EXPERIMENT_DIR", "experiments_ab")
AB_ACTIVE_ID_FILE = os.path.join(AB_EXPERIMENT_DIR, "active_ab_experiment_id.txt")


@dataclass
class ABExperimentConfig:
    """
    Immutable configuration for an A/B forward validation experiment.
    """
    # Identity
    experiment_id: str = "ab_ai_advisory_001"
    experiment_name: str = "AI Advisory vs Baseline A/B Forward Test"
    created_at: float = field(default_factory=time.time)
    git_sha: str = "UNKNOWN"
    status: str = "INITIALIZED"  # "INITIALIZED" | "RUNNING" | "COMPLETED" | "HALTED"
    started_at: float | None = None
    ended_at: float | None = None
    termination_reason: str | None = None

    # Arms
    arm_a_name: str = "CONTROL_BASELINE"
    arm_b_name: str = "TREATMENT_AI_ADVISED"

    # Common Market Parameters
    symbols: list[str] = field(default_factory=lambda: [
        "BTCUSDT", "ETHUSDT", "SOLUSDT", "LINKUSDT",
        "BNBUSDT", "DOGEUSDT", "ADAUSDT", "DOTUSDT"
    ])
    timeframe: str = "1h"
    strategy_name: str = "strategy_swing"

    # Capital & Sizing
    initial_capital_per_arm: float = 10000.0
    max_position_pct: float = 0.10
    max_simultaneous_positions: int = 3
    max_drawdown_limit_pct: float = 0.10  # 10% drawdown hard stop per arm
    leverage: float = 1.0

    # Cost Model (Binance Taker fees & realistic slippage)
    cost_config: dict[str, float] = field(default_factory=lambda: {
        "entry_fee": 0.001,
        "exit_fee": 0.001,
        "entry_slip": 0.0005,
        "exit_slip": 0.0005,
        "spread": 0.0001
    })

    # Statistical Evaluation Criteria
    planned_duration_days: int = 30
    min_trades_for_significance: int = 30
    alpha_significance_threshold: float = 0.05
    required_profit_factor_arm_b: float = 1.20

    # File Namespaces
    # Arm A (Control)
    state_file_control: str = "paper_state_control.json"
    ledger_file_control: str = "paper_trade_ledger_control.jsonl"
    equity_file_control: str = "paper_equity_curve_control.jsonl"
    signals_file_control: str = "paper_signals_control.jsonl"

    # Arm B (Treatment)
    state_file_treatment: str = "paper_state_treatment.json"
    ledger_file_treatment: str = "paper_trade_ledger_treatment.jsonl"
    equity_file_treatment: str = "paper_equity_curve_treatment.jsonl"
    signals_file_treatment: str = "paper_signals_treatment.jsonl"
    params_state_treatment: str = "advisory_params_state_treatment.json"
    advisory_log_treatment: str = "advisory_log_treatment.jsonl"

    def mark_started(self) -> None:
        self.status = "RUNNING"
        self.started_at = time.time()

    def mark_ended(self, reason: str = "COMPLETED") -> None:
        self.status = "COMPLETED" if "COMPLETED" in reason else "HALTED"
        self.ended_at = time.time()
        self.termination_reason = reason

    def save(self, directory: str | None = None) -> str:
        d = directory or AB_EXPERIMENT_DIR
        os.makedirs(d, exist_ok=True)
        path = os.path.join(d, f"{self.experiment_id}.json")
        tmp_path = path + ".tmp"
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(asdict(self), f, indent=2)
        os.replace(tmp_path, path)
        return path

    @classmethod
    def load(cls, filepath: str) -> "ABExperimentConfig":
        with open(filepath, "r", encoding="utf-8") as f:
            data = json.load(f)
        return cls(**data)


def get_default_ab_config() -> ABExperimentConfig:
    """Returns the default pre-registered A/B configuration."""
    git_sha = "UNKNOWN"
    try:
        import subprocess
        git_sha = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], stderr=subprocess.DEVNULL
        ).decode().strip()
    except Exception:
        pass

    return ABExperimentConfig(
        experiment_id="ab_ai_advisory_001",
        experiment_name="AI Advisory vs Baseline A/B Forward Test",
        git_sha=git_sha
    )
