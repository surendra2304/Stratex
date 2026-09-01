"""
evolution/genetic_engine.py — Complete Genetic Strategy Evolution Engine.

Features:
1. StrategyGenome dataclass:
   - strategy_type: "trend" | "mean_reversion" | "momentum" | "breakout"
   - indicators: combinations from ["RSI", "MACD", "EMA", "Bollinger", "ADX", "Supertrend", "VWAP", "ATR"]
   - parameters: dictionary of periods, thresholds, multipliers
   - entry_logic: indicator combination rules
   - exit_logic: stop_loss, take_profit, trailing_stop, timed_exit
   - risk_params: position_size_method, max_risk_pct
   - fitness: computed from walk-forward backtest results (Sharpe * PF)
2. Evolution operations:
   - Mutation: random parameter perturbation ±10-30% per gene with 20% gene selection probability.
   - Crossover: single-point crossover combining parent genomes preserving strategy_type from fitter parent.
   - Selection: tournament selection (size 5) keeping top 50% of population.
3. Population management:
   - Maintains 80 genomes per generation.
   - Evolve weekly on rolling window.
   - Archives retired genomes with full performance history.
"""

import copy
import datetime
import json
import random
import time
from dataclasses import dataclass, field
from typing import Any

from logger import get_logger

logger = get_logger("genetic_engine")

AVAILABLE_INDICATORS = ["RSI", "MACD", "EMA", "Bollinger", "ADX", "Supertrend", "VWAP", "ATR"]
STRATEGY_TYPES = ["trend", "mean_reversion", "momentum", "breakout"]
SIZING_METHODS = ["volatility_target", "fixed_fractional", "risk_parity", "half_kelly"]


@dataclass
class StrategyGenome:
    genome_id: str
    strategy_type: str  # "trend", "mean_reversion", "momentum", "breakout"
    indicators: list[str] = field(default_factory=lambda: ["RSI", "EMA", "ATR"])
    parameters: dict[str, Any] = field(default_factory=dict)
    entry_logic: dict[str, Any] = field(default_factory=dict)
    exit_logic: dict[str, Any] = field(default_factory=dict)
    risk_params: dict[str, Any] = field(default_factory=dict)
    fitness: float = 0.0
    generation: int = 1
    created_at: str = field(default_factory=lambda: datetime.datetime.utcnow().isoformat() + "Z")
    performance_history: dict[str, Any] = field(default_factory=dict)
    feedback_notes: list[str] = field(default_factory=list)


class StrategyGeneticEngine:
    """
    Manages population lifecycle, genetic mutation, crossover, selection, and archival.
    """

    def __init__(
        self,
        population_size: int = 80,
        mutation_rate: float = 0.20,
        tournament_size: int = 5,
        archive_file: str = "retired_genomes_archive.jsonl"
    ):
        self.population_size = population_size
        self.mutation_rate = mutation_rate
        self.tournament_size = tournament_size
        self.archive_file = archive_file
        self.generation = 1
        self.population: list[StrategyGenome] = self._initialize_population()
        self.history: list[dict[str, Any]] = []

    def _initialize_population(self) -> list[StrategyGenome]:
        pop = []
        for i in range(self.population_size):
            stype = random.choice(STRATEGY_TYPES)
            num_indicators = random.randint(2, 4)
            chosen_inds = random.sample(AVAILABLE_INDICATORS, num_indicators)

            params = {
                "rsi_period": random.randint(8, 28),
                "rsi_oversold": round(random.uniform(20.0, 35.0), 1),
                "rsi_overbought": round(random.uniform(65.0, 80.0), 1),
                "ema_fast": random.randint(5, 20),
                "ema_slow": random.randint(21, 60),
                "adx_threshold": round(random.uniform(18.0, 32.0), 1),
                "bb_window": random.randint(14, 30),
                "bb_std": round(random.uniform(1.5, 2.5), 1),
                "atr_period": random.randint(10, 24),
                "atr_multiplier": round(random.uniform(1.5, 3.5), 1),
                "supertrend_period": random.randint(7, 14),
                "supertrend_multiplier": round(random.uniform(2.0, 4.0), 1)
            }

            entry_logic = {
                "require_all_indicators": random.choice([True, False]),
                "trend_filter": "EMA" in chosen_inds,
                "momentum_filter": "RSI" in chosen_inds,
                "volatility_breakout": "Bollinger" in chosen_inds
            }

            exit_logic = {
                "stop_loss_pct": round(random.uniform(0.01, 0.035), 3),
                "take_profit_pct": round(random.uniform(0.02, 0.08), 3),
                "use_trailing_stop": random.choice([True, False]),
                "trailing_activation_pct": round(random.uniform(0.015, 0.04), 3),
                "max_hold_bars": random.randint(48, 288)
            }

            risk_params = {
                "position_size_method": random.choice(SIZING_METHODS),
                "max_risk_pct": round(random.uniform(0.005, 0.02), 3)
            }

            genome = StrategyGenome(
                genome_id=f"GEN_{self.generation}_STRAT_{i+1:03d}",
                strategy_type=stype,
                indicators=chosen_inds,
                parameters=params,
                entry_logic=entry_logic,
                exit_logic=exit_logic,
                risk_params=risk_params,
                generation=self.generation
            )
            pop.append(genome)
        return pop

    def mutate(self, genome: StrategyGenome) -> StrategyGenome:
        """Applies random parameter perturbation (±10-30%) per gene with 20% gene selection probability."""
        child = copy.deepcopy(genome)
        child.genome_id = f"GEN_{self.generation+1}_MUT_{int(time.time()*1000)%10000:04d}"
        child.generation = self.generation + 1
        child.created_at = datetime.datetime.utcnow().isoformat() + "Z"

        for k, val in child.parameters.items():
            if random.random() < self.mutation_rate:
                perturbation = random.uniform(0.10, 0.30) * random.choice([-1.0, 1.0])
                if isinstance(val, int):
                    new_val = int(round(val * (1.0 + perturbation)))
                    child.parameters[k] = max(2, new_val)
                elif isinstance(val, float):
                    new_val = round(val * (1.0 + perturbation), 3)
                    child.parameters[k] = max(0.001, new_val)

        for k, val in child.exit_logic.items():
            if isinstance(val, float) and random.random() < self.mutation_rate:
                perturbation = random.uniform(0.10, 0.30) * random.choice([-1.0, 1.0])
                child.exit_logic[k] = round(max(0.002, min(0.15, val * (1.0 + perturbation))), 3)

        return child

    def crossover(self, parent_a: StrategyGenome, parent_b: StrategyGenome) -> StrategyGenome:
        """Single-point crossover combining parent genomes, preserving strategy_type from the fitter parent."""
        fitter_parent = parent_a if parent_a.fitness >= parent_b.fitness else parent_b
        other_parent = parent_b if fitter_parent == parent_a else parent_a

        # Combine indicators
        combined_indicators = list(set(fitter_parent.indicators[:2] + other_parent.indicators[:2]))

        # Crossover parameters dictionary
        params = {}
        all_keys = list(fitter_parent.parameters.keys())
        split_idx = len(all_keys) // 2
        for k in all_keys[:split_idx]:
            params[k] = fitter_parent.parameters.get(k, 14)
        for k in all_keys[split_idx:]:
            params[k] = other_parent.parameters.get(k, 14)

        child = StrategyGenome(
            genome_id=f"GEN_{self.generation+1}_CROSS_{int(time.time()*1000)%10000:04d}",
            strategy_type=fitter_parent.strategy_type,
            indicators=combined_indicators,
            parameters=params,
            entry_logic=copy.deepcopy(fitter_parent.entry_logic),
            exit_logic=copy.deepcopy(fitter_parent.exit_logic),
            risk_params=copy.deepcopy(fitter_parent.risk_params),
            generation=self.generation + 1,
            created_at=datetime.datetime.utcnow().isoformat() + "Z"
        )
        return child

    def tournament_selection(self) -> StrategyGenome:
        """Selects best performer from tournament of size k=5."""
        candidates = random.sample(self.population, min(self.tournament_size, len(self.population)))
        return max(candidates, key=lambda g: g.fitness)

    def archive_retired_genome(self, genome: StrategyGenome, reason: str = "SELECTION_RETIRED") -> None:
        """Appends retired genome with full performance history to append-only archive."""
        record = {
            "genome_id": genome.genome_id,
            "strategy_type": genome.strategy_type,
            "generation": genome.generation,
            "fitness": genome.fitness,
            "retired_at": datetime.datetime.utcnow().isoformat() + "Z",
            "retirement_reason": reason,
            "parameters": genome.parameters,
            "performance_history": genome.performance_history,
            "feedback_notes": genome.feedback_notes
        }
        try:
            with open(self.archive_file, "a", encoding="utf-8") as f:
                f.write(json.dumps(record) + "\n")
        except Exception as e:
            logger.error(f"[GENETIC_ENGINE] Failed to archive retired genome: {e}")

    def evolve_generation(self) -> list[StrategyGenome]:
        """Keeps top 50% of population, breeds offspring, and archives bottom 50%."""
        sorted_pop = sorted(self.population, key=lambda g: g.fitness, reverse=True)
        keep_count = self.population_size // 2  # Top 50%

        survivors = sorted_pop[:keep_count]
        retired = sorted_pop[keep_count:]

        # Archive retired genomes
        for r in retired:
            self.archive_retired_genome(r, reason="BOTTOM_50_PCT_RETIRED")

        # Breed next generation to maintain population size
        next_gen = [copy.deepcopy(g) for g in survivors]
        while len(next_gen) < self.population_size:
            parent1 = self.tournament_selection()
            parent2 = self.tournament_selection()
            child = self.crossover(parent1, parent2)
            child = self.mutate(child)
            next_gen.append(child)

        self.history.append({
            "generation": self.generation,
            "top_fitness": survivors[0].fitness if survivors else 0.0,
            "mean_fitness": float(sum(g.fitness for g in survivors) / len(survivors)) if survivors else 0.0,
            "timestamp": datetime.datetime.utcnow().isoformat() + "Z"
        })

        self.generation += 1
        self.population = next_gen
        logger.info(f"[GENETIC_ENGINE] 🧬 Evolved generation {self.generation} ({len(self.population)} genomes).")
        return self.population
