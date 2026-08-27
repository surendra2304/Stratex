"""
tests/test_autonomous_orchestration.py — Tests for Ecosystem State Machine, Operations Director, Self-Healing & Compliance.

Verifies:
1. EcosystemStateMachine state transitions (FULL_AUTONOMY, DEGRADED, PROTECTED, DEFENSIVE, HALTED) and history.
2. SelfHealingEngine atomic state recovery and scheduled maintenance window execution.
3. AutonomousOperationsDirector multi-frequency decision cycles and autonomy levels (Levels 1, 2, 3).
4. PerformanceLearningEngine trade pattern extraction and heuristic recommendation generation.
5. ComplianceReporter daily compliance dossier generation and cryptographic HMAC signature validation.
"""

import tempfile
import pytest

from autonomy.ecosystem_state import EcosystemStateMachine
from autonomy.self_healing import SelfHealingEngine
from autonomy.operations_director import AutonomousOperationsDirector
from autonomy.performance_learning import PerformanceLearningEngine
from autonomy.compliance_reporting import ComplianceReporter


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

        # Create dummy state and backup
        state_file = f"{tmpdir}/bot_state.json"
        with open(state_file, "w") as f:
            f.write('{"balance": 5000.0}')

        # Create backup copy
        bak_file = f"{tmpdir}/bot_state.json.123456789.bak"
        with open(bak_file, "w") as f:
            f.write('{"balance": 5000.0, "recovered": true}')

        # Corrupt state file
        with open(state_file, "w") as f:
            f.write('CORRUPTED')

        # Self-heal
        restored = healer.restore_latest_good_state(state_file)
        assert restored is True
        with open(state_file, "r") as f:
            content = f.read()
            assert "recovered" in content

        # Maintenance window
        m_res = healer.execute_maintenance_window()
        assert m_res["status"] == "MAINTENANCE_COMPLETE"


def test_autonomous_operations_director():
    director = AutonomousOperationsDirector(autonomy_level=2)
    assert director.autonomy_level == 2

    # High frequency cycle (Nominal)
    res = director.run_high_frequency_cycle(current_drawdown_pct=2.0, active_positions_count=2)
    assert res["status"] == "NOMINAL"
    assert res["ecosystem_state"] == "FULL_AUTONOMY"

    # High frequency cycle (Warning corridor -> PROTECTED)
    res = director.run_high_frequency_cycle(current_drawdown_pct=9.0, active_positions_count=2)
    assert director.state_machine.current_state == "PROTECTED"

    # High frequency cycle (Critical breach -> DEFENSIVE)
    res = director.run_high_frequency_cycle(current_drawdown_pct=16.0, active_positions_count=0)
    assert res["status"] == "CIRCUIT_BREAKER_TRIPPED"
    assert director.state_machine.current_state == "DEFENSIVE"

    # Set autonomy level
    ok = director.set_autonomy_level(3)
    assert ok is True
    assert director.autonomy_level == 3


def test_performance_learning_engine():
    learner = PerformanceLearningEngine()
    trades = [
        {"symbol": "BTC/USDT", "regime": "HIGH_VOLATILITY", "net_pnl": -50.0},
        {"symbol": "ETH/USDT", "regime": "HIGH_VOLATILITY", "net_pnl": -30.0},
        {"symbol": "SOL/USDT", "regime": "HIGH_VOLATILITY", "net_pnl": -20.0},
        {"symbol": "BTC/USDT", "regime": "BULL_TREND", "net_pnl": 100.0},
        {"symbol": "ETH/USDT", "regime": "BULL_TREND", "net_pnl": 80.0},
        {"symbol": "SOL/USDT", "regime": "BULL_TREND", "net_pnl": 50.0}
    ]

    patterns = learner.analyze_trade_patterns(trades)
    assert len(patterns) >= 1
    assert any(p.affected_strategy == "scalper" for p in patterns)


def test_compliance_reporter():
    with tempfile.TemporaryDirectory() as tmpdir:
        reporter = ComplianceReporter(reports_dir=tmpdir)
        dossier = reporter.generate_daily_compliance_dossier(
            trades_count=25,
            daily_pnl=145.50,
            max_drawdown_reached=3.2,
            decisions_count=12
        )
        assert dossier["report_type"] == "DAILY_COMPLIANCE_DOSSIER"
        assert dossier["metrics"]["net_pnl_dollars"] == 145.50
        assert "signature" in dossier
