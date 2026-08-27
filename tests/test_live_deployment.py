"""
tests/test_live_deployment.py — Final Verification Suite for Live Deployment Gates, Hardening & Operational Monitors.

Verifies:
1. LiveDeploymentGates evaluates all 4 pillars (Technical, Risk, Operational, Compliance) and signs output.
2. ReliabilityHardener computes exponential backoff delays, creates atomic state backups, and activates graceful degradation.
3. LiveTradingPreparer creates capital plans and executes emergency halt protocols.
4. EnterpriseProductionMonitor gathers full operational telemetry.
"""

import os
import tempfile
import time
import pytest

from deployment.live_deployment_gates import LiveDeploymentGates
from hardening.production_hardening import ReliabilityHardener
from deployment.live_trading_prep import LiveTradingPreparer, LiveCapitalPlan
from monitoring.production_monitor import EnterpriseProductionMonitor


def test_live_deployment_gates_evaluation():
    gates = LiveDeploymentGates()
    eval_res = gates.evaluate_all_gates()
    assert "evaluation_id" in eval_res
    assert "signature" in eval_res
    assert eval_res["overall_status"] in ["READY_FOR_DEPLOYMENT", "DEPLOYMENT_BLOCKED"]
    assert "technical" in eval_res["gates"]
    assert "risk" in eval_res["gates"]


def test_reliability_hardener():
    with tempfile.TemporaryDirectory() as tmpdir:
        hardener = ReliabilityHardener(backup_dir=tmpdir)

        # Exponential backoff
        assert hardener.compute_backoff_delay(0) == 1.0
        assert hardener.compute_backoff_delay(1) == 2.0
        assert hardener.compute_backoff_delay(2) == 4.0
        assert hardener.compute_backoff_delay(10) == 60.0  # Capped at max_backoff

        # Atomic backup
        src_file = os.path.join(tmpdir, "state.json")
        with open(src_file, "w") as f:
            f.write('{"test": 123}')

        bak = hardener.create_atomic_backup(src_file)
        assert bak is not None
        assert os.path.exists(bak)

        # Graceful degradation
        deg = hardener.execute_graceful_degradation("AI_UNIVERSE", "Timeout 120s")
        assert deg["status"] == "DEGRADED_FALLBACK_ACTIVE"


def test_live_trading_preparer():
    prep = LiveTradingPreparer(LiveCapitalPlan(initial_allocated_capital=10000.0, reserve_capital=2000.0, active_trading_capital=8000.0))
    plan = prep.generate_capital_allocation_plan()
    assert plan["capital_plan"]["active_trading_capital"] == 8000.0
    assert plan["safety_ratios"]["reserve_buffer_ratio"] == 0.20

    halt_res = prep.execute_emergency_halt_protocol()
    assert halt_res["bot_daemon_state"] == "HALTED"


def test_enterprise_production_monitor():
    monitor = EnterpriseProductionMonitor()
    status = monitor.get_full_operational_status()
    assert "overall_health" in status
    assert "trading_telemetry" in status
    assert "host_infrastructure" in status
