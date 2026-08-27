"""
evolution/incubator.py — Paper Trading Strategy Incubator & Graduation Lifecycle.

Tracks:
1. Paper incubation lifecycle for gauntlet-certified strategies (minimum 30 calendar days).
2. Live vs Backtest Fidelity Tracking: Evaluates correlation between live paper signals and backtest models.
3. Graduation Criteria:
   - >= 30 days active incubation
   - Profit Factor >= 1.25
   - Max Live Drawdown <= 10.0%
   - Fidelity Score >= 0.70 (Live returns match theoretical curve)
"""

import time
import datetime
import json
import os
from typing import Dict, List, Optional, Tuple, Any
from dataclasses import dataclass, field, asdict
from evolution.genetic_engine import StrategyGenome


@dataclass
class IncubatingStrategy:
    genome_id: str
    archetype: str
    admission_timestamp: float
    incubation_days: int = 0
    live_trades_count: int = 0
    live_net_pnl: float = 0.0
    live_profit_factor: float = 1.0
    live_max_drawdown_pct: float = 0.0
    fidelity_score: float = 0.85
    status: str = "INCUBATING"  # "INCUBATING", "GRADUATED", "REJECTED"


class StrategyIncubator:
    """
    Manages the forward paper validation and graduation pipeline for evolved strategies.
    """

    def __init__(self, state_file: str = "incubator_state.json"):
        self.state_file = state_file
        self.incubating_pool: Dict[str, IncubatingStrategy] = {}
        self.load_state()

    def load_state(self) -> None:
        if os.path.exists(self.state_file):
            try:
                with open(self.state_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    for gid, item in data.items():
                        self.incubating_pool[gid] = IncubatingStrategy(**item)
            except Exception:
                pass

    def save_state(self) -> None:
        data = {gid: asdict(s) for gid, s in self.incubating_pool.items()}
        with open(self.state_file, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)

    def admit_strategy(self, genome: StrategyGenome) -> IncubatingStrategy:
        """Admits a gauntlet-certified strategy into the paper incubator."""
        strat = IncubatingStrategy(
            genome_id=genome.genome_id,
            archetype=genome.archetype,
            admission_timestamp=time.time()
        )
        self.incubating_pool[genome.genome_id] = strat
        self.save_state()
        return strat

    def update_strategy_performance(
        self,
        genome_id: str,
        incubation_days: int,
        trades_count: int,
        net_pnl: float,
        profit_factor: float,
        max_dd_pct: float,
        fidelity_score: float = 0.80
    ) -> Optional[IncubatingStrategy]:
        """Updates live paper tracking telemetry."""
        if genome_id not in self.incubating_pool:
            return None

        strat = self.incubating_pool[genome_id]
        strat.incubation_days = incubation_days
        strat.live_trades_count = trades_count
        strat.live_net_pnl = net_pnl
        strat.live_profit_factor = profit_factor
        strat.live_max_drawdown_pct = max_dd_pct
        strat.fidelity_score = fidelity_score

        # Check Graduation Criteria
        if (
            strat.incubation_days >= 30 and
            strat.live_profit_factor >= 1.25 and
            strat.live_max_drawdown_pct <= 10.0 and
            strat.fidelity_score >= 0.70
        ):
            strat.status = "GRADUATED"
        elif strat.live_max_drawdown_pct > 15.0 or (strat.incubation_days >= 30 and strat.live_profit_factor < 1.0):
            strat.status = "REJECTED"

        self.save_state()
        return strat

    def get_graduated_strategies(self) -> List[IncubatingStrategy]:
        """Returns strategies that met all graduation criteria for production deployment."""
        return [s for s in self.incubating_pool.values() if s.status == "GRADUATED"]
