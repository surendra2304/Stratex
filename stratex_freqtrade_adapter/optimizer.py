"""Optuna optimizer for Stratex strategy configs.

The optimizer expects a callable that:
    run_fn(params: dict) -> dict

The returned dict should include at least:
    net_pnl, profit_factor, max_drawdown_pct, trade_count

The objective strongly penalizes drawdown, costs and statistically weak runs.
"""

from __future__ import annotations

import datetime
import json
import math
import os
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

try:
    import optuna
except ImportError:
    optuna = None


def get_git_commit_sha() -> str:
    """Safely retrieves current git commit SHA if available."""
    try:
        res = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
            timeout=5,
        )
        return res.stdout.strip()
    except Exception:
        return "UNKNOWN"


@dataclass
class OptimizationConfig:
    n_trials: int = 100
    seed: int | None = 42
    min_trades: int = 30
    max_drawdown_pct: float = 0.05
    target_profit_factor: float = 1.20
    study_name: str = "stratex_profitability"
    direction: str = "maximize"
    output_file: str = "optimization_results/best_params.json"
    strategy_name: str = "adx_ema"
    timeframe: str = "4h"
    symbols: list[str] = field(default_factory=lambda: ["BTCUSDT"])
    promotion_status: str = "RESEARCH ONLY"


class StrategyOptimizer:
    def __init__(self, config: OptimizationConfig):
        self.config = config
        if optuna is None:
            raise RuntimeError("Optuna is required. Install with: pip install optuna")

    @staticmethod
    def objective_from_result(result: dict, cfg: OptimizationConfig) -> float:
        trades = int(result.get("trade_count", result.get("total_trades", 0)))
        pf = float(result.get("profit_factor", 0.0))
        pnl = float(result.get("net_pnl", 0.0))
        
        # Handle max drawdown whether passed as percentage (e.g. 5.0) or fraction (0.05)
        raw_dd = float(result.get("max_drawdown_pct", result.get("max_dd_pct", 1.0)))
        dd = raw_dd / 100.0 if raw_dd > 1.0 else raw_dd

        sharpe = float(result.get("sharpe", 0.0))
        sortino = float(result.get("sortino", 0.0))

        if trades < cfg.min_trades:
            return -1e6 + trades
        if not all(math.isfinite(x) for x in (pf, pnl, dd, sharpe)):
            return -1e6

        penalty = max(0.0, dd - cfg.max_drawdown_pct) * 20000.0
        weak_pf_penalty = max(0.0, cfg.target_profit_factor - pf) * 10000.0
        
        score = pnl + (100.0 * sharpe) + (50.0 * sortino) + (1000.0 * max(0.0, pf - 1.0)) - penalty - weak_pf_penalty
        return score

    def optimize(
        self,
        suggest_params: Callable[[Any], dict],
        run_fn: Callable[[dict], dict],
    ) -> dict:
        sampler = optuna.samplers.TPESampler(seed=self.config.seed)
        study = optuna.create_study(
            direction=self.config.direction,
            sampler=sampler,
            study_name=self.config.study_name,
        )

        def _objective(trial):
            params = suggest_params(trial)
            result = run_fn(params)
            score = self.objective_from_result(result, self.config)
            trial.set_user_attr("result", result)
            return score

        study.optimize(_objective, n_trials=self.config.n_trials)

        best = {
            "strategy": self.config.strategy_name,
            "study_name": self.config.study_name,
            "git_sha": get_git_commit_sha(),
            "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "timeframe": self.config.timeframe,
            "symbols": self.config.symbols,
            "promotion_status": self.config.promotion_status,
            "score": round(study.best_value, 4),
            "params": study.best_params,
            "result": study.best_trial.user_attrs.get("result", {}),
            "n_trials": len(study.trials),
            "config": {
                "min_trades": self.config.min_trades,
                "max_drawdown_pct": self.config.max_drawdown_pct,
                "target_profit_factor": self.config.target_profit_factor,
                "seed": self.config.seed,
            },
        }

        path = Path(self.config.output_file)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(best, indent=2, default=str), encoding="utf-8")
        return best

