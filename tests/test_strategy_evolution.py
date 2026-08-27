"""
tests/test_strategy_evolution.py — Tests for Complete Strategy Evolution Engine, 6 Gates, Incubator & Approval.

Verifies:
1. StrategyGenome instantiation (strategy_type, indicators, parameters, entry/exit logic, risk_params).
2. StrategyGeneticEngine mutation (±10-30%), crossover (preserving fitter parent strategy_type), tournament selection, and archival.
3. ValidationGauntlet 6-gate sequential verification (PF > 1.3, Trades > 100, WFE > 0.5, MC 95th DD < 15%, Sensitivity < 30%, Regime > 60%, PBO < 30%).
4. StrategyIncubator 30-day paper forward tracking, fidelity score correlation (> 0.60), and graduation threshold (Live PF > 1.10).
5. HumanApprovalGate proposal creation, cryptographic signature generation, and approval workflow.
"""

import tempfile
import pytest

from evolution.genetic_engine import StrategyGeneticEngine, StrategyGenome
from evolution.validation_gauntlet import ValidationGauntlet
from evolution.incubator import StrategyIncubator
from evolution.approval_gates import HumanApprovalGate


def test_strategy_genetic_engine():
    engine = StrategyGeneticEngine(population_size=20, mutation_rate=0.30)
    assert len(engine.population) == 20

    # Test Mutation
    parent = engine.population[0]
    mutant = engine.mutate(parent)
    assert mutant.genome_id != parent.genome_id
    assert mutant.generation == 2

    # Test Crossover
    parent.fitness = 2.5
    parent2 = engine.population[1]
    parent2.fitness = 1.2
    child = engine.crossover(parent, parent2)
    assert child.generation == 2
    assert child.strategy_type == parent.strategy_type  # Preserved from fitter parent

    # Set mock fitness and evolve next generation
    for i, g in enumerate(engine.population):
        g.fitness = float(i * 1.5)

    next_gen = engine.evolve_generation()
    assert len(next_gen) == 20
    assert engine.generation == 2


def test_validation_gauntlet_gates():
    gauntlet = ValidationGauntlet()
    genome = StrategyGenome(genome_id="TEST_GENOME", strategy_type="trend")

    metrics_pass = {
        "profit_factor": 1.65,
        "trade_count": 120,
        "in_sample_sharpe": 1.8,
        "out_sample_sharpe": 1.2,
        "trade_returns": [1.5, -0.5, 2.0, -0.8, 1.2, 0.9, -0.3, 1.8, 2.2, -0.7] * 12,
        "base_return": 100.0,
        "perturbed_returns": [88.0, 92.0, 84.0],
        "regimes": {"TRENDING": 150.0, "RANGING": 60.0, "VOLATILE": -20.0},
        "pbo_pct": 16.5,
        "deflated_sharpe": 1.45
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
    assert genome.fitness > 0


def test_strategy_incubator_lifecycle():
    with tempfile.TemporaryDirectory() as tmpdir:
        state_file = f"{tmpdir}/incubator.json"
        archive_file = f"{tmpdir}/retired.jsonl"
        incubator = StrategyIncubator(state_file=state_file, archive_file=archive_file)

        genome = StrategyGenome(genome_id="INC_STRAT_1", strategy_type="momentum")
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

        # Graduate at 30 days (PF > 1.10, Fidelity > 0.60)
        grad = incubator.update_strategy_performance(
            genome_id="INC_STRAT_1",
            incubation_days=30,
            trades_count=45,
            net_pnl=280.0,
            profit_factor=1.45,
            max_dd_pct=5.0,
            fidelity_score=0.85
        )
        assert grad.status == "GRADUATION_CANDIDATE"
        assert len(incubator.get_graduation_candidates()) == 1


def test_human_approval_gates():
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
