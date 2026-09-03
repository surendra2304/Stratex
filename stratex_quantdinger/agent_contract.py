"""Safe research/agent boundary.

AI agents may submit backtest and optimization jobs and inspect results.
They do NOT have access to direct exchange order placement from this interface.
"""

from __future__ import annotations
from typing import Any
from .jobs import JobStore, ResearchJobRunner
from .registry import StrategyRegistry


class ResearchAgentGateway:
    """Safe boundary for autonomous agents and external research interfaces."""

    def __init__(self, store: JobStore | None = None, registry: StrategyRegistry | None = None, runner: ResearchJobRunner | None = None):
        self.store = store or JobStore()
        self.registry = registry or StrategyRegistry()
        self.runner = runner or ResearchJobRunner(store=self.store, registry=self.registry)

    def submit_backtest(self, job_id: str, strategy_id: str, parameters: dict[str, Any] | None = None) -> dict[str, Any]:
        """Submits a durable backtest job."""
        job = self.store.create(
            job_id=job_id,
            job_type="BACKTEST",
            metadata={"strategy_id": strategy_id, "parameters": parameters or {}}
        )
        return job.__dict__

    def submit_optimization(self, job_id: str, strategy_id: str, n_trials: int = 35) -> dict[str, Any]:
        """Submits a durable hyperparameter optimization job."""
        job = self.store.create(
            job_id=job_id,
            job_type="OPTIMIZATION",
            metadata={"strategy_id": strategy_id, "n_trials": n_trials}
        )
        return job.__dict__

    def submit_walk_forward(self, job_id: str, strategy_id: str, windows: int = 4) -> dict[str, Any]:
        """Submits a durable walk-forward validation job."""
        job = self.store.create(
            job_id=job_id,
            job_type="WALK_FORWARD",
            metadata={"strategy_id": strategy_id, "windows": windows}
        )
        return job.__dict__

    def get_job(self, job_id: str) -> dict[str, Any]:
        """Inspects status, progress, and results of a research job."""
        return self.store.get(job_id).__dict__

    def list_jobs(self, job_type: str | None = None, status: str | None = None) -> list[dict[str, Any]]:
        """Lists research jobs."""
        return [j.__dict__ for j in self.store.list_jobs(job_type=job_type, status=status)]

    def list_strategy_versions(self, strategy_id: str | None = None) -> list[dict[str, Any]]:
        """Lists registered immutable strategy versions."""
        return [v.__dict__ for v in self.registry.list_versions(strategy_id=strategy_id)]
