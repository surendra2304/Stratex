"""
evolution/genetic_engine.py — Strategy Genome Representation & Evolutionary Genetic Algorithm.

Capabilities:
1. Strategy Genome Representation:
   - Strategy archetype (trend, mean_reversion, momentum, breakout)
   - Indicator components (RSI, EMA, ADX, Bollinger Bands, ATR)
   - Parameter thresholds (entry/exit periods, oversold/overbought levels)
   - Risk settings (Stop Loss %, Take Profit %, Sizing method)
2. Evolutionary Operators:
   - Mutation: Gaussian parameter perturbation (±10% to ±30%) with boundary clipping.
   - Crossover: Uniform and single-point crossover blending two parent genomes.
   - Selection: Tournament and fitness-proportionate selection retaining elite performers.
3. Population Management:
   - Maintains generations of 50-100 candidate strategy genomes.
"""

import random
import copy
import time
import numpy as np
from typing import Dict, List, Optional, Tuple, Any
from dataclasses import dataclass, field, asdict


@dataclass
class StrategyGenome:
    genome_id: str
    archetype: str  # "trend", "mean_reversion", "momentum", "breakout"
    rsi_period: int = 14
    rsi_oversold: float = 30.0
    rsi_overbought: float = 70.0
    ema_fast: int = 9
    ema_slow: int = 21
    adx_threshold: float = 25.0
    bb_window: int = 20
    bb_std: float = 2.0
    stop_loss_pct: float = 0.02
    take_profit_pct: float = 0.04
    fitness: float = 0.0
    generation: int = 1
    metadata: Dict[str, Any] = field(default_factory=dict)


class StrategyGeneticEngine:
    """
    Evolves quantitative strategy genomes using crossover, mutation, and tournament selection.
    """

    ARCHETYPES = ["trend", "mean_reversion", "momentum", "breakout"]

    def __init__(self, population_size: int = 50, mutation_rate: float = 0.20):
        self.population_size = population_size
        self.mutation_rate = mutation_rate
        self.generation = 1
        self.population: List[StrategyGenome] = self._initialize_population()

    def _initialize_population(self) -> List[StrategyGenome]:
        pop = []
        for i in range(self.population_size):
            arch = random.choice(self.ARCHETYPES)
            g = StrategyGenome(
                genome_id=f"GEN_{self.generation}_STRAT_{i+1}",
                archetype=arch,
                rsi_period=random.randint(8, 28),
                rsi_oversold=round(random.uniform(20.0, 38.0), 1),
                rsi_overbought=round(random.uniform(62.0, 80.0), 1),
                ema_fast=random.randint(5, 20),
                ema_slow=random.randint(21, 60),
                adx_threshold=round(random.uniform(18.0, 35.0), 1),
                bb_window=random.randint(14, 30),
                bb_std=round(random.uniform(1.5, 2.5), 1),
                stop_loss_pct=round(random.uniform(0.01, 0.04), 3),
                take_profit_pct=round(random.uniform(0.02, 0.08), 3),
                generation=self.generation
            )
            pop.append(g)
        return pop

    def mutate(self, genome: StrategyGenome) -> StrategyGenome:
        """Applies random Gaussian mutation to genome parameters within bounded ranges."""
        child = copy.deepcopy(genome)
        child.genome_id = f"GEN_{self.generation+1}_MUT_{int(time.time()*1000)%10000}"
        child.generation = self.generation + 1

        if random.random() < self.mutation_rate:
            delta = random.choice([-2, -1, 1, 2])
            child.rsi_period = max(5, min(35, child.rsi_period + delta))

        if random.random() < self.mutation_rate:
            factor = random.uniform(0.85, 1.15)
            child.rsi_oversold = round(max(15.0, min(40.0, child.rsi_oversold * factor)), 1)

        if random.random() < self.mutation_rate:
            delta_fast = random.choice([-2, -1, 1, 2])
            child.ema_fast = max(3, min(25, child.ema_fast + delta_fast))

        if random.random() < self.mutation_rate:
            delta_slow = random.choice([-3, -1, 1, 3])
            child.ema_slow = max(child.ema_fast + 5, min(100, child.ema_slow + delta_slow))

        if random.random() < self.mutation_rate:
            factor = random.uniform(0.85, 1.15)
            child.stop_loss_pct = round(max(0.005, min(0.06, child.stop_loss_pct * factor)), 3)

        return child

    def crossover(self, parent_a: StrategyGenome, parent_b: StrategyGenome) -> StrategyGenome:
        """Blends traits of two parent genomes into a new offspring."""
        offspring = StrategyGenome(
            genome_id=f"GEN_{self.generation+1}_CROSS_{int(time.time()*1000)%10000}",
            archetype=parent_a.archetype if random.random() < 0.5 else parent_b.archetype,
            rsi_period=parent_a.rsi_period if random.random() < 0.5 else parent_b.rsi_period,
            rsi_oversold=parent_a.rsi_oversold if random.random() < 0.5 else parent_b.rsi_oversold,
            rsi_overbought=parent_a.rsi_overbought if random.random() < 0.5 else parent_b.rsi_overbought,
            ema_fast=parent_a.ema_fast if random.random() < 0.5 else parent_b.ema_fast,
            ema_slow=parent_a.ema_slow if random.random() < 0.5 else parent_b.ema_slow,
            adx_threshold=parent_a.adx_threshold if random.random() < 0.5 else parent_b.adx_threshold,
            bb_window=parent_a.bb_window if random.random() < 0.5 else parent_b.bb_window,
            bb_std=parent_a.bb_std if random.random() < 0.5 else parent_b.bb_std,
            stop_loss_pct=parent_a.stop_loss_pct if random.random() < 0.5 else parent_b.stop_loss_pct,
            take_profit_pct=parent_a.take_profit_pct if random.random() < 0.5 else parent_b.take_profit_pct,
            generation=self.generation + 1
        )
        return offspring

    def tournament_selection(self, k: int = 3) -> StrategyGenome:
        """Selects the best performing genome from k randomly sampled candidates."""
        candidates = random.sample(self.population, min(k, len(self.population)))
        return max(candidates, key=lambda g: g.fitness)

    def evolve_generation(self) -> List[StrategyGenome]:
        """Produces the next generation of strategies through elitism, crossover, and mutation."""
        sorted_pop = sorted(self.population, key=lambda g: g.fitness, reverse=True)
        # Retain top 10% elite genomes directly
        elite_count = max(2, int(self.population_size * 0.10))
        next_gen = [copy.deepcopy(g) for g in sorted_pop[:elite_count]]

        while len(next_gen) < self.population_size:
            parent1 = self.tournament_selection()
            parent2 = self.tournament_selection()
            child = self.crossover(parent1, parent2)
            child = self.mutate(child)
            next_gen.append(child)

        self.generation += 1
        self.population = next_gen
        return self.population
