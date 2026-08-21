"""
paper_engine/experiment_config.py

Frozen experiment configuration for genuine forward validation.

CRITICAL CONTRACT:
- Once an experiment is started, its configuration is IMMUTABLE.
- Any modification to strategy, CostEngine, risk limits, symbols, or model
  version MUST start a NEW experiment with a new experiment_id.
- The original experiment results must NEVER be overwritten.
- The configuration is persisted to disk atomically at experiment start.
- The Git SHA is captured at start time for exact reproducibility.
"""
import json
import os
import time
import uuid
from dataclasses import asdict, dataclass, field

from logger import get_logger

logger = get_logger("experiment_config")


@dataclass
class FrozenExperimentConfig:
    """
    Immutable configuration for a single forward validation experiment.
    Fields are captured at start time and must not be changed thereafter.
    """
    # Identity
    experiment_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    experiment_name: str = "unnamed_experiment"
    created_at: float = field(default_factory=time.time)
    git_sha: str = "UNKNOWN"

    # Strategy
    strategy_name: str = "UNSPECIFIED"
    strategy_version: str = "1.0"
    strategy_params: dict = field(default_factory=dict)

    # Symbol universe — frozen, no additions after start
    symbols: list[str] = field(default_factory=list)
    timeframe: str = "1m"

    # Cost model — must match the actual strategy's CostEngine exactly
    cost_config: dict = field(default_factory=dict)

    # Position sizing / risk
    starting_capital: float = 10000.0
    max_position_pct: float = 0.10
    max_simultaneous_positions: int = 3
    max_daily_loss: float = 500.0
    max_drawdown_pct: float = 0.20
    leverage: float = 1.0

    # Hold period
    typical_hold_bars: int = 5

    # Benchmark parameters — frozen so they cannot be post-hoc adjusted
    benchmark_hold_bars: int = 5
    benchmark_n_trades: int = 10
    benchmark_iterations: int = 1000
    benchmark_random_seed: int = 42

    # Forward validation parameters
    min_required_trades: int = 30  # minimum trades for any statistical inference
    planned_duration_days: int = 30

    # Acceptance criteria (pre-registered — cannot be changed after start)
    required_profit_factor: float = 1.2      # Gross profit / Gross loss
    required_expectancy_per_trade: float = 0.0  # Must be > 0 after all fees
    required_win_rate: float = 0.0           # No minimum; expectancy is the gate
    required_sharpe: float = 0.5            # Minimum Sharpe over forward period
    max_acceptable_drawdown_pct: float = 0.20

    # Status tracking
    status: str = "NOT_STARTED"  # NOT_STARTED | RUNNING | COMPLETED | ABORTED
    started_at: float | None = None
    completed_at: float | None = None
    abort_reason: str | None = None

    def to_dict(self) -> dict:
        return asdict(self)

    def save(self, directory: str = "experiments"):
        """Atomically write the frozen config to disk."""
        os.makedirs(directory, exist_ok=True)
        path = os.path.join(directory, f"{self.experiment_id}.json")
        tmp_path = path + ".tmp"
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(self.to_dict(), f, indent=4)
        os.replace(tmp_path, path)
        logger.info(f"Experiment config saved: {path}")
        return path

    @classmethod
    def load(cls, experiment_id: str, directory: str = "experiments") -> "FrozenExperimentConfig":
        path = os.path.join(directory, f"{experiment_id}.json")
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return cls(**data)

    def mark_started(self):
        """Call exactly once when the forward experiment begins collecting live data."""
        if self.status != "NOT_STARTED":
            raise RuntimeError(f"Cannot start experiment '{self.experiment_id}': already {self.status}")
        self.status = "RUNNING"
        self.started_at = time.time()

    def mark_completed(self):
        if self.status != "RUNNING":
            raise RuntimeError(f"Cannot complete experiment '{self.experiment_id}': not RUNNING")
        self.status = "COMPLETED"
        self.completed_at = time.time()

    def mark_aborted(self, reason: str):
        self.status = "ABORTED"
        self.completed_at = time.time()
        self.abort_reason = reason


def register_experiment(config: FrozenExperimentConfig, registry_path: str = "experiments/registry.json"):
    """Register an experiment in the immutable registry."""
    try:
        with open(registry_path, "r") as f:
            registry = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        registry = {"experiments": []}

    # Prevent duplicate registration
    existing_ids = [e["experiment_id"] for e in registry["experiments"]]
    if config.experiment_id in existing_ids:
        logger.warning(f"Experiment {config.experiment_id} already in registry — not re-registering.")
        return

    registry["experiments"].append({
        "experiment_id": config.experiment_id,
        "experiment_name": config.experiment_name,
        "strategy_name": config.strategy_name,
        "git_sha": config.git_sha,
        "created_at": config.created_at,
        "status": config.status,
    })

    tmp = registry_path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(registry, f, indent=4)
    os.replace(tmp, registry_path)
    logger.info(f"Experiment {config.experiment_id} registered in {registry_path}")


def _get_git_sha() -> str:
    try:
        import subprocess
        r = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True, timeout=5)
        return r.stdout.strip() if r.returncode == 0 else "UNKNOWN"
    except Exception:
        return "UNKNOWN"


def create_experiment(
    name: str,
    strategy_name: str,
    symbols: list[str],
    strategy_params: dict,
    cost_engine,
    starting_capital: float = 10000.0,
    timeframe: str = "1m",
    planned_duration_days: int = 30,
) -> FrozenExperimentConfig:
    """
    Factory: create and register a new frozen experiment config.
    Captures the Git SHA automatically for traceability.
    """
    git_sha = _get_git_sha()
    config = FrozenExperimentConfig(
        experiment_name=name,
        strategy_name=strategy_name,
        strategy_params=strategy_params,
        symbols=symbols,
        timeframe=timeframe,
        cost_config=cost_engine.get_report_dict() if cost_engine else {},
        starting_capital=starting_capital,
        git_sha=git_sha,
        planned_duration_days=planned_duration_days,
    )
    config.save()
    register_experiment(config)
    logger.info(
        f"Created experiment '{name}' [id={config.experiment_id[:8]}] "
        f"git_sha={git_sha[:8]} symbols={symbols}"
    )
    return config
