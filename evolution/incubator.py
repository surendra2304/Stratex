"""
evolution/incubator.py — Paper Trading Strategy Incubator & Graduation Lifecycle.

Incubation Rules:
1. Minimum 30 calendar days of paper forward trading.
2. Tracks live-vs-backtest fidelity score (correlation between live signals and backtest expectation).
3. Graduation Requirements:
   - Live Profit Factor > 1.10
   - Fidelity Score > 0.60
   - Zero hard risk limit violations
4. Failure Action: Strategy retired, genome archived with empirical learnings.
"""

import json
import os
import time
from dataclasses import asdict, dataclass, field

from evolution.genetic_engine import StrategyGenome
from logger import get_logger

logger = get_logger("incubator")


@dataclass
class IncubatingStrategy:
    genome_id: str
    strategy_type: str
    admission_timestamp: float
    incubation_days: int = 0
    live_trades_count: int = 0
    live_net_pnl: float = 0.0
    live_profit_factor: float = 1.0
    live_max_drawdown_pct: float = 0.0
    fidelity_score: float = 0.85
    risk_violations_count: int = 0
    status: str = "INCUBATING"  # "INCUBATING", "GRADUATION_CANDIDATE", "RETIRED"
    learnings: list[str] = field(default_factory=list)


class StrategyIncubator:
    """
    Manages the forward paper validation and graduation pipeline for gauntlet-certified strategies.
    """

    def __init__(self, state_file: str = "incubator_state.json", archive_file: str = "incubator_retired_archive.jsonl"):
        self.state_file = state_file
        self.archive_file = archive_file
        self.incubating_pool: dict[str, IncubatingStrategy] = {}
        self.load_state()

    def load_state(self) -> None:
        if os.path.exists(self.state_file):
            try:
                with open(self.state_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    for gid, item in data.items():
                        self.incubating_pool[gid] = IncubatingStrategy(**item)
            except Exception as e:
                logger.error(f"[INCUBATOR] Failed to load state: {e}")

    def save_state(self) -> None:
        data = {gid: asdict(s) for gid, s in self.incubating_pool.items()}
        with open(self.state_file, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)

    def admit_strategy(self, genome: StrategyGenome) -> IncubatingStrategy:
        """Admits a gauntlet-certified strategy into the paper incubator."""
        strat = IncubatingStrategy(
            genome_id=genome.genome_id,
            strategy_type=genome.strategy_type,
            admission_timestamp=time.time()
        )
        self.incubating_pool[genome.genome_id] = strat
        self.save_state()
        logger.info(f"[INCUBATOR] 🐣 Admitted strategy {genome.genome_id} into incubation.")
        return strat

    def update_strategy_performance(
        self,
        genome_id: str,
        incubation_days: int,
        trades_count: int,
        net_pnl: float,
        profit_factor: float,
        max_dd_pct: float,
        fidelity_score: float = 0.75,
        risk_violations: int = 0
    ) -> IncubatingStrategy | None:
        """Updates forward paper tracking telemetry and evaluates graduation/retirement."""
        if genome_id not in self.incubating_pool:
            return None

        strat = self.incubating_pool[genome_id]
        strat.incubation_days = incubation_days
        strat.live_trades_count = trades_count
        strat.live_net_pnl = net_pnl
        strat.live_profit_factor = profit_factor
        strat.live_max_drawdown_pct = max_dd_pct
        strat.fidelity_score = fidelity_score
        strat.risk_violations_count = risk_violations

        # Graduation Criteria: 30+ days, PF > 1.10, Fidelity > 0.60, 0 risk violations
        if (
            strat.incubation_days >= 30 and
            strat.live_profit_factor >= 1.10 and
            strat.fidelity_score >= 0.60 and
            strat.risk_violations_count == 0 and
            strat.live_max_drawdown_pct <= 15.0
        ):
            strat.status = "GRADUATION_CANDIDATE"
            logger.info(f"[INCUBATOR] 🎓 Strategy {genome_id} achieved GRADUATION_CANDIDATE status!")
        elif (
            strat.risk_violations_count > 0 or
            strat.live_max_drawdown_pct > 15.0 or
            (strat.incubation_days >= 30 and (strat.live_profit_factor < 1.10 or strat.fidelity_score < 0.60))
        ):
            strat.status = "RETIRED"
            strat.learnings.append(f"Failed incubation: PF={profit_factor:.2f}, Fidelity={fidelity_score:.2f}, DD={max_dd_pct:.1f}%")
            self._archive_failed_strategy(strat)
            logger.warning(f"[INCUBATOR] ❌ Strategy {genome_id} RETIRED from incubation.")

        self.save_state()
        return strat

    def _archive_failed_strategy(self, strat: IncubatingStrategy) -> None:
        try:
            with open(self.archive_file, "a", encoding="utf-8") as f:
                f.write(json.dumps(asdict(strat)) + "\n")
        except Exception:
            pass

    def get_graduation_candidates(self) -> list[IncubatingStrategy]:
        """Returns strategies ready for human promotion review."""
        return [s for s in self.incubating_pool.values() if s.status == "GRADUATION_CANDIDATE"]
