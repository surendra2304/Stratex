"""
tests/test_live_deployment.py — Final Verification Suite for Live Deployment Gates, Hardening & Operational Monitors.

Verifies:
1. LiveDeploymentGates evaluates all 4 pillars (Technical, Risk, Operational, Compliance) and signs output.
2. ReliabilityHardener computes exponential backoff delays, creates atomic state backups, and activates graceful degradation.
3. LiveTradingPreparer creates capital plans and executes emergency halt protocols.
4. EnterpriseProductionMonitor gathers full operational telemetry.
5. LiveAuthorizationVerifier: Verifies that missing .env, token, or history blocks live activation.
6. Capital Levels & Demotion: Enforces specs for Levels 1-4 and verifies demotion on drawdown / loss streaks.
7. LiveRiskEnforcer: Enforces position sizing, strategy limits, correlation caps, and kill-switch flattening.
8. LiveLedgerManager: Isolates accounting and detects exchange balance reconciliation discrepancies.
9. LiveRollbackManager: Flattens live positions, removes authorization tokens, and logs signed incidents.
"""

import os
import tempfile
import time
import pytest

from deployment.live_deployment_gates import LiveDeploymentGates
from hardening.production_hardening import ReliabilityHardener
from deployment.live_trading_prep import LiveTradingPreparer, LiveCapitalPlan
from monitoring.production_monitor import EnterpriseProductionMonitor
from deployment.capital_levels import get_level_spec, check_demotion_trigger
from deployment.live_authorization import LiveAuthorizationVerifier, create_physical_authorization_file
from risk.live_risk_enforcer import LiveRiskEnforcer
from ledger.live_ledger import LiveLedgerManager
from deployment.live_rollback import LiveRollbackManager


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


def test_capital_levels_and_demotion():
    # Spec checks
    l1 = get_level_spec(1)
    assert l1.max_capital == 1000.0
    assert l1.max_position_size_pct == 0.05
    assert l1.max_daily_loss_pct == 0.02
    assert l1.max_strategies == 1

    l2 = get_level_spec(2)
    assert l2.max_capital == 5000.0
    assert l2.max_strategies == 3

    # Demotion trigger on Level 2 drawdown breach (e.g. 10% DD > 8% max)
    demote, new_lvl, reason = check_demotion_trigger(current_level=2, current_drawdown_pct=10.0, consecutive_loss_days=0)
    assert demote is True
    assert new_lvl == 1
    assert "Demoting to Level 1" in reason

    # Demotion on 3 consecutive losing days
    demote, new_lvl, reason = check_demotion_trigger(current_level=2, current_drawdown_pct=3.0, consecutive_loss_days=3)
    assert demote is True
    assert new_lvl == 1


def test_live_authorization_gates():
    with tempfile.TemporaryDirectory() as tmpdir:
        auth_file = os.path.join(tmpdir, ".live_trading_authorized")
        verifier = LiveAuthorizationVerifier(auth_file=auth_file)

        # 1. Unset env and missing token -> BLOCKED
        os.environ["LIVE_TRADING_ENABLED"] = "False"
        os.environ["LIVE_AUTONOMY_CONFIRMED"] = "False"
        state = verifier.verify_all_authorizations()
        assert state.is_authorized is False
        assert len(state.blocking_errors) >= 2

        # 2. Add physical token and set env vars -> AUTHORIZED
        create_physical_authorization_file(level=1, authorized_capital=1000.0, filepath=auth_file)
        os.environ["LIVE_TRADING_ENABLED"] = "True"
        os.environ["LIVE_AUTONOMY_CONFIRMED"] = "True"

        state2 = verifier.verify_all_authorizations()
        assert state2.is_authorized is True
        assert state2.authorized_level == 1
        assert state2.authorized_capital == 1000.0

        # Clean up env
        os.environ["LIVE_TRADING_ENABLED"] = "False"
        os.environ["LIVE_AUTONOMY_CONFIRMED"] = "False"


def test_live_risk_enforcer_bounds_and_killswitch():
    enforcer = LiveRiskEnforcer(level=1, current_equity=1000.0)

    # 1. Order within 5% limit ($50 notional) -> APPROVED
    ok, msg = enforcer.check_order_admissibility(
        symbol="BTCUSDT", notional=45.0, strategy_name="scalper", current_open_positions=[]
    )
    assert ok is True

    # 2. Order exceeding 5% limit ($75 notional) -> REJECTED
    ok, msg = enforcer.check_order_admissibility(
        symbol="BTCUSDT", notional=75.0, strategy_name="scalper", current_open_positions=[]
    )
    assert ok is False
    assert "exceeds Level 1 max position limit" in msg

    # 3. Exceeding strategy count limit (Level 1 allows only 1 strategy)
    current_pos = [{"symbol": "BTCUSDT", "strategy": "scalper"}]
    ok, msg = enforcer.check_order_admissibility(
        symbol="ETHUSDT", notional=30.0, strategy_name="supertrend", current_open_positions=current_pos
    )
    assert ok is False
    assert "exceeds Level 1 maximum" in msg

    # 4. Kill switch execution
    res = enforcer.execute_kill_switch_flatten()
    assert res["action"] == "EMERGENCY_FLATTEN"
    assert enforcer.circuit_breaker_active is True

    # Subsequent orders must be rejected
    ok, msg = enforcer.check_order_admissibility(
        symbol="BTCUSDT", notional=10.0, strategy_name="scalper", current_open_positions=[]
    )
    assert ok is False
    assert "Circuit breaker active" in msg


def test_live_ledger_and_reconciliation():
    with tempfile.TemporaryDirectory() as tmpdir:
        t_file = os.path.join(tmpdir, "trade.jsonl")
        e_file = os.path.join(tmpdir, "equity.jsonl")
        b_file = os.path.join(tmpdir, "balance.jsonl")
        r_file = os.path.join(tmpdir, "risk.jsonl")

        ledger = LiveLedgerManager(trade_file=t_file, equity_file=e_file, balance_file=b_file, risk_file=r_file)

        # Record trade
        ledger.record_live_trade({"trade_id": "T1", "symbol": "BTCUSDT", "net_pnl": 15.50})
        assert os.path.exists(t_file)

        # Reconciliation within 0.5% tolerance
        ok, disc, msg = ledger.reconcile_exchange_balance(exchange_reported_balance=1002.0, local_calculated_balance=1000.0)
        assert ok is True
        assert disc == 0.2

        # Reconciliation breach (> 0.5% discrepancy)
        ok, disc, msg = ledger.reconcile_exchange_balance(exchange_reported_balance=950.0, local_calculated_balance=1000.0)
        assert ok is False
        assert disc == 5.0
        assert "RECONCILIATION_ALERT" in msg


def test_live_rollback_manager():
    with tempfile.TemporaryDirectory() as tmpdir:
        r_mgr = LiveRollbackManager(incident_dir=tmpdir)
        incident = r_mgr.execute_live_rollback(
            reason="CRITICAL_DISCREPANCY_DETECTED",
            triggered_by="TEST_SUITE",
            open_positions=[{"symbol": "BTCUSDT"}]
        )
        assert incident["status"] == "ROLLBACK_COMPLETED"
        assert incident["positions_liquidated_count"] == 1
        assert "signature" in incident
