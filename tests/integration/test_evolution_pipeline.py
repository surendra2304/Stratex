from evolution.approval_gates import HumanApprovalGate
from evolution.genetic_engine import StrategyGenome
from evolution.incubator import StrategyIncubator
from evolution.validation_gauntlet import ValidationGauntlet


def test_full_evolution_pipeline_stages(tmp_path):
    # 1. Genome creation
    genome = StrategyGenome(genome_id='PIPE_GENOME_001', strategy_type='trend')

    # 2. Validation Gauntlet (6 gates)
    gauntlet = ValidationGauntlet()
    metrics_pass = {
        'profit_factor': 1.55,
        'trade_count': 125,
        'in_sample_sharpe': 1.9,
        'out_sample_sharpe': 1.3,
        'trade_returns': [1.2, -0.4, 1.8, -0.6, 1.0, 0.8, -0.2, 1.5, 2.0, -0.5] * 13,
        'base_return': 100.0,
        'perturbed_returns': [90.0, 93.0, 86.0],
        'regimes': {'TRENDING': 140.0, 'RANGING': 50.0, 'VOLATILE': -10.0},
        'pbo_pct': 14.0,
        'deflated_sharpe': 1.50
    }
    cert = gauntlet.run_full_gauntlet(genome, metrics_pass)
    assert cert['all_gates_passed'] is True

    # 3. Incubation (Simulated 30 Days)
    inc_state = str(tmp_path / 'incubator.json')
    inc_archive = str(tmp_path / 'archive.jsonl')
    incubator = StrategyIncubator(state_file=inc_state, archive_file=inc_archive)
    incubator.admit_strategy(genome)
    grad = incubator.update_strategy_performance(
        genome_id='PIPE_GENOME_001',
        incubation_days=30,
        trades_count=50,
        net_pnl=350.0,
        profit_factor=1.42,
        max_dd_pct=4.8,
        fidelity_score=0.86
    )
    assert grad.status == 'GRADUATION_CANDIDATE'

    # 4. Human Approval Gate
    gate_state = str(tmp_path / 'approval.json')
    gate = HumanApprovalGate(state_file=gate_state)
    prop = gate.submit_promotion_proposal(
        genome_id='PIPE_GENOME_001',
        archetype='trend',
        evidence_summary=cert,
        incubation_days=30,
        live_pf=1.42,
        fidelity=0.86
    )
    approved = gate.approve_proposal(prop.proposal_id, approver='CHIEF_QUANT', rationale='Clean 30d paper validation')
    assert approved.status == 'APPROVED'
    assert approved.signature is not None
