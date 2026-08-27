"""
tests/test_strategy_evolution.py — Tests for Strategy Genetic Engine, Validation Gauntlet & Incubator.

Verifies:
1. StrategyGeneticEngine genome initialization, Gaussian mutation, crossover, tournament selection, and generational evolution.
2. ValidationGauntlet 6-gate evaluation (Profitability, WFE, Monte Carlo drawdown, Sensitivity, Regime robustness, Overfitting).
3. StrategyIncubator admission, forward tracking, fidelity score correlation, and graduation criteria.
"""

import tempfile
import pytest

from evolution.genetic_engine import StrategyGeneticEngine, StrategyGenome
from evolution.validation_gauntlet import ValidationGauntlet
from evolution.incubator import StrategyIncubator


def test_strategy_genetic_engine():
    engine = StrategyGeneticEngine(population_size=20, mutation_rate=0.30)
    assert len(engine.population) == 20

    # Test Mutation
    parent = engine.population[0]
    mutant = engine.mutate(parent)
    assert mutant.genome_id != parent.genome_id
    assert mutant.generation == 2

    # Test Crossover
    parent2 = engine.population[1]
    child = engine.crossover(parent, parent2)
    assert child.generation == 2

    # Set mock fitness and evolve next generation
    for i, g in enumerate(engine.population):
        g.fitness = float(i * 1.5)

    next_gen = engine.evolve_generation()
    assert len(next_gen) == 20
    assert engine.generation == 2


def test_validation_gauntlet_gates():
    gauntlet = ValidationGauntlet()
    genome = StrategyGenome(genome_id="TEST_GENOME", archetype="trend")

    metrics_pass = {
        "profit_factor": 1.65,
        "trade_count": 80,
        "in_sample_sharpe": 1.8,
        "out_sample_sharpe": 1.2,
        "trade_returns": [1.5, -0.5, 2.0, -0.8, 1.2, 0.9, -0.3, 1.8, 2.2, -0.7],
        "regimes": {"BULL_TREND": 150.0, "BEAR_TREND": 60.0, "CHOP": -20.0, "HIGH_VOL": 40.0}
    }

    cert = gauntlet.run_full_gauntlet(genome, metrics_pass)
    assert cert["all_gates_passed"] is True
    assert cert["overall_status"] == "GAUNTLET_CERTIFIED"
    assert cert["gates"]["gate_1"]["passed"] is True
    assert cert["gates"]["gate_2"]["passed"] is True
    assert cert["gates"]["gate_3"]["passed"] is True
    assert cert["gates"]["gate_4"]["passed"] is True
    assert cert["gates"]["gate_5"]["passed"] is True
    assert cert["gates"]["gate_6"]["passed"] is True


def test_strategy_incubator_lifecycle():
    with tempfile.TemporaryDirectory() as tmpdir:
        state_file = f"{tmpdir}/incubator.json"
        incubator = StrategyIncubator(state_file=state_file)

        genome = StrategyGenome(genome_id="INC_STRAT_1", archetype="momentum")
        strat = incubator.admit_strategy(genome)
        assert strat.status == "INCUBATING"

        # Update within incubation (e.g. 15 days -> still INCUBATING)
        incubator.update_strategy_performance(
            genome_id="INC_STRAT_1",
            incubation_days=15,
            trades_count=20,
            net_pnl=120.0,
            profit_factor=1.40,
            max_dd_pct=4.5,
            fidelity_score=0.88
        )
        assert incubator.incubating_pool["INC_STRAT_1"].status == "INCUBATING"

        # Graduate at 30 days
        grad = incubator.update_strategy_performance(
            genome_id="INC_STRAT_1",
            incubation_days=30,
            trades_count=45,
            net_pnl=280.0,
            profit_factor=1.45,
            max_dd_pct=5.0,
            fidelity_score=0.85
        )
        assert grad.status == "GRADUATED"
        assert len(incubator.get_graduated_strategies()) == 1


def test_human_approval_gates():
    from evolution.approval_gates import HumanApprovalGate
    with tempfile.TemporaryDirectory() as tmpdir:
        gate_file = f"{tmpdir}/approval_queue.json"
        gate = HumanApprovalGate(state_file=gate_file)

        # 1. Propose promotion
        prop = gate.submit_promotion_proposal(
            genome_id="EVO_STRAT_007",
            archetype="breakout",
            evidence_summary={"pf": 1.45, "fidelity": 0.82},
            incubation_days=31,
            live_pf=1.42,
            fidelity=0.82
        )
        assert prop.status == "PENDING_HUMAN_APPROVAL"
        assert len(gate.get_pending_proposals()) == 1

        # 2. Human approval
        approved_prop = gate.approve_proposal(prop.proposal_id, approver="HUMAN_OPERATOR", rationale="Exceptional walk-forward fidelity.")
        assert approved_prop.status == "APPROVED"
        assert approved_prop.signature is not None
        assert len(gate.get_pending_proposals()) == 0
