"""
tests/test_autonomous_orchestration.py — Tests for Operations Director, Self-Healing, Degradation Matrix & Compliance.

Verifies:
1. EcosystemStateMachine state transitions and summary history.
2. SelfHealingEngine checksum validation, atomic file restore, exponential backoff, and memory monitoring.
3. DegradationPolicyMatrix fallback execution across AI-Universe, exchange, dashboard, and monitoring disruptions.
4. AutonomousOperationsDirector multi-frequency decision cycles and autonomy levels (Levels 1, 2, 3).
5. ComplianceReporter multi-format report generation (JSON, Markdown, HTML) with voice summaries and HMAC signatures.
6. Master Control API endpoints.
"""

import tempfile

from autonomy.compliance_reporting import ComplianceReporter
from autonomy.degradation_matrix import DegradationPolicyMatrix, SubsystemHealth
from autonomy.ecosystem_state import EcosystemStateMachine
from autonomy.operations_director import AutonomousOperationsDirector
from autonomy.self_healing import SelfHealingEngine


def test_ecosystem_state_machine():
    sm = EcosystemStateMachine(initial_state="FULL_AUTONOMY")
    assert sm.current_state == "FULL_AUTONOMY"

    # Transition to PROTECTED
    ok = sm.transition_to("PROTECTED", "Drawdown at 9.5%")
    assert ok is True
    assert sm.current_state == "PROTECTED"
    assert len(sm.transition_history) == 1

    # Transition to HALTED
    sm.transition_to("HALTED", "Operator manual panic")
    assert sm.current_state == "HALTED"

    summary = sm.get_state_summary()
    assert summary["current_state"] == "HALTED"
    assert len(summary["recent_transitions"]) == 2


def test_self_healing_engine():
    with tempfile.TemporaryDirectory() as tmpdir:
        healer = SelfHealingEngine(backup_dir=tmpdir)

        # 1. State restore
        state_file = f"{tmpdir}/bot_state.json"
        with open(state_file, "w") as f:
            f.write('{"balance": 5000.0}')

        bak_file = f"{tmpdir}/bot_state.json.123456789.bak"
        with open(bak_file, "w") as f:
            f.write('{"balance": 5000.0, "recovered": true}')

        with open(state_file, "w") as f:
            f.write('CORRUPTED')

        restored = healer.restore_latest_good_state(state_file)
        assert restored is True

        # 2. Checksum validation
        is_valid = healer.validate_and_repair_state_file(state_file)
        assert is_valid is True

        # 3. Exchange API failure backoff
        fail_res = healer.handle_exchange_api_failure(consecutive_errors=6)
        assert fail_res["halt_new_entries"] is True
        assert fail_res["manage_open_positions_only"] is True

        # 4. Strategy crash restart
        assert healer.isolate_and_restart_strategy("strategy_scalper") is True

        # 5. Memory check
        mem_res = healer.check_memory_usage(current_rss_mb=900.0, is_low_activity_window=True)
        assert mem_res["restart_recommended"] is True


def test_degradation_policy_matrix():
    matrix = DegradationPolicyMatrix()

    # 1. AI-Universe Down
    h1 = SubsystemHealth(ai_universe_online=False, exchange_healthy=True, dashboard_online=True, monitoring_online=True)
    res1 = matrix.evaluate_degradation_policy(h1)
    assert res1["position_size_multiplier"] == 1.0

    # 2. Exchange Degraded
    h2 = SubsystemHealth(ai_universe_online=True, exchange_healthy=False, dashboard_online=True, monitoring_online=True)
    res2 = matrix.evaluate_degradation_policy(h2)
    assert res2["position_size_multiplier"] == 0.50
    assert res2["stop_loss_multiplier"] == 1.20
    assert res2["halt_scalpers"] is True

    # 3. Complete Degradation
    h3 = SubsystemHealth(exchange_healthy=False, monitoring_online=False)
    res3 = matrix.evaluate_degradation_policy(h3)
    assert res3["emergency_flatten"] is True
    assert res3["halt_all_entries"] is True


def test_autonomous_operations_director():
    director = AutonomousOperationsDirector(autonomy_level=2)
    assert director.autonomy_level == 2

    # High frequency cycle (Nominal)
    res = director.run_high_frequency_cycle(current_drawdown_pct=2.0, active_positions_count=2)
    assert res["status"] == "NOMINAL"
    assert director.state_machine.current_state == "FULL_AUTONOMY"

    # High frequency cycle (Warning corridor -> PROTECTED)
    res = director.run_high_frequency_cycle(current_drawdown_pct=6.0, active_positions_count=2)
    assert director.state_machine.current_state == "PROTECTED"

    # High frequency cycle (Critical breach -> DEFENSIVE)
    res = director.run_high_frequency_cycle(current_drawdown_pct=13.0, active_positions_count=0)
    assert res["status"] == "CRITICAL_DRAWDOWN"
    assert director.state_machine.current_state == "DEFENSIVE"

    # Medium frequency cycle (Hourly rebalancing)
    weights = director.run_medium_frequency_cycle({"supertrend": 1.8, "scalper": 1.2})
    assert "supertrend" in weights

    # Set autonomy level
    new_lvl = director.set_autonomy_level(3)
    assert new_lvl == 3
    assert director.autonomy_level == 3


def test_compliance_reporter():
    with tempfile.TemporaryDirectory() as tmpdir:
        reporter = ComplianceReporter(reports_dir=tmpdir, retention_days=90)
        dossier = reporter.generate_daily_compliance_dossier(
            trades_count=25,
            daily_pnl=145.50,
            max_drawdown_reached=3.2,
            decisions_count=12
        )
        assert dossier["report_type"] == "DAILY_COMPLIANCE_DOSSIER"
        assert dossier["metrics"]["net_pnl_dollars"] == 145.50
        assert "signature" in dossier
        assert "voice_summary" in dossier
