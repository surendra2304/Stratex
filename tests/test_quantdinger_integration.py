"""Tests for QuantDinger architectural integration into STRATEX."""

import json
import pytest
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from stratex_quantdinger.models import (
    StrategyVersion,
    ExperimentJob,
    RuntimeHeartbeat,
    ExecutionIntent,
    AuditEvent,
)
from stratex_quantdinger.registry import StrategyRegistry
from stratex_quantdinger.jobs import JobStore, ResearchJobRunner
from stratex_quantdinger.runtime import RuntimeLease, RuntimeSupervisor
from stratex_quantdinger.idempotency import IdempotencyGuard
from stratex_quantdinger.agent_contract import ResearchAgentGateway
from execution import ExecutionPolicy


# ==============================================================================
# 1. STRATEGY REGISTRY & IMMUTABLE VERSIONING
# ==============================================================================

def test_strategy_registry_registration_and_hash(tmp_path):
    reg_file = str(tmp_path / "registry.json")
    audit_file = str(tmp_path / "audit.jsonl")
    registry = StrategyRegistry(path=reg_file, audit_log_path=audit_file)

    source = "def run(): return 'strategy_v1'"
    params = {"ema_fast": 20, "ema_slow": 50}

    ver = registry.register(
        strategy_id="adx_ema",
        version="v1.0.0",
        source=source,
        parameters=params,
        status="RESEARCH",
    )

    assert ver.strategy_id == "adx_ema"
    assert ver.version == "v1.0.0"
    assert ver.status == "RESEARCH"
    assert len(ver.source_hash) == 64  # SHA-256
    assert ver.parameters == params

    # Duplicate registration of identical version returns existing
    ver_dup = registry.register(
        strategy_id="adx_ema",
        version="v1.0.0",
        source=source,
        parameters=params,
        status="RESEARCH",
    )
    assert ver_dup.version == ver.version
    assert ver_dup.source_hash == ver.source_hash


def test_strategy_registry_immutable_conflict(tmp_path):
    reg_file = str(tmp_path / "registry.json")
    registry = StrategyRegistry(path=reg_file)

    source1 = "def run(): return 1"
    params = {"p": 1}
    registry.register("strat", "v1.0.0", source1, params)

    # Different source code under same version MUST raise ValueError
    source2 = "def run(): return 2"
    with pytest.raises(ValueError, match="Immutable strategy version conflict"):
        registry.register("strat", "v1.0.0", source2, params)

    # Different parameters under same version MUST raise ValueError
    with pytest.raises(ValueError, match="Immutable strategy version conflict"):
        registry.register("strat", "v1.0.0", source1, {"p": 2})


def test_strategy_lifecycle_promotion_stages(tmp_path):
    reg_file = str(tmp_path / "registry.json")
    audit_file = str(tmp_path / "audit.jsonl")
    registry = StrategyRegistry(path=reg_file, audit_log_path=audit_file)

    registry.register("trend", "v1.0.0", "source", {"sl": 2.0}, status="RESEARCH")

    # Cannot jump directly from RESEARCH to ACTIVE
    with pytest.raises(ValueError, match="Illegal lifecycle transition"):
        registry.promote("trend", "v1.0.0", "ACTIVE")

    # Step 1: RESEARCH -> OOS_VALIDATED
    v1 = registry.promote("trend", "v1.0.0", "OOS_VALIDATED", actor="researcher", reason="Passed 4 walk-forward splits")
    assert v1.status == "OOS_VALIDATED"

    # Step 2: OOS_VALIDATED -> APPROVED
    v2 = registry.promote("trend", "v1.0.0", "APPROVED", actor="risk_officer", reason="Approved for staging")
    assert v2.status == "APPROVED"

    # Step 3: APPROVED -> ACTIVE
    v3 = registry.promote("trend", "v1.0.0", "ACTIVE", actor="operator", reason="Deployed to testnet runtime")
    assert v3.status == "ACTIVE"

    # Step 4: ACTIVE -> RETIRED
    v4 = registry.promote("trend", "v1.0.0", "RETIRED", actor="operator", reason="Replaced by v2")
    assert v4.status == "RETIRED"

    # Audit events logged
    assert Path(audit_file).exists()
    lines = Path(audit_file).read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) >= 5  # 1 register + 4 transitions


def test_strategy_single_active_version_rule(tmp_path):
    reg_file = str(tmp_path / "registry.json")
    registry = StrategyRegistry(path=reg_file)

    registry.register("strat", "v1.0.0", "source 1", {}, status="RESEARCH")
    registry.promote("strat", "v1.0.0", "OOS_VALIDATED")
    registry.promote("strat", "v1.0.0", "APPROVED")
    registry.promote("strat", "v1.0.0", "ACTIVE")

    assert registry.get_active("strat").version == "v1.0.0"

    # Register and activate v2.0.0
    registry.register("strat", "v2.0.0", "source 2", {}, status="RESEARCH")
    registry.promote("strat", "v2.0.0", "OOS_VALIDATED")
    registry.promote("strat", "v2.0.0", "APPROVED")
    registry.promote("strat", "v2.0.0", "ACTIVE")

    # v2.0.0 should now be ACTIVE and v1.0.0 automatically RETIRED
    assert registry.get_active("strat").version == "v2.0.0"
    assert registry.get("strat", "v1.0.0").status == "RETIRED"


# ==============================================================================
# 2. RESEARCH JOBS & RUNNER
# ==============================================================================

def test_job_store_lifecycle_and_progress(tmp_path):
    jobs_file = str(tmp_path / "jobs.json")
    audit_file = str(tmp_path / "audit.jsonl")
    store = JobStore(path=jobs_file, audit_log_path=audit_file)

    job = store.create("job_opt_1", "OPTIMIZATION", metadata={"trials": 20})
    assert job.job_id == "job_opt_1"
    assert job.status == "QUEUED"
    assert job.progress == 0.0

    # Progress update
    store.update("job_opt_1", status="RUNNING", progress=0.5)
    j_updated = store.get("job_opt_1")
    assert j_updated.status == "RUNNING"
    assert j_updated.progress == 0.5

    # Completion with results
    store.update("job_opt_1", status="COMPLETED", progress=1.0, result={"best_pf": 1.45})
    j_done = store.get("job_opt_1")
    assert j_done.status == "COMPLETED"
    assert j_done.result["best_pf"] == 1.45

    # Job listing
    all_jobs = store.list_jobs(job_type="OPTIMIZATION")
    assert len(all_jobs) == 1
    assert all_jobs[0].job_id == "job_opt_1"


def test_research_job_runner_execution(tmp_path):
    jobs_file = str(tmp_path / "jobs.json")
    store = JobStore(path=jobs_file)
    runner = ResearchJobRunner(store=store)

    def sample_worker(s, j_id):
        s.update(j_id, progress=0.8)
        s.update(j_id, status="COMPLETED", progress=1.0, result={"score": 99})

    job = runner.submit_and_execute_async("job_test_1", "BACKTEST", sample_worker)
    assert job.status == "QUEUED"

    # Wait for background thread completion
    time.sleep(0.3)
    completed_job = store.get("job_test_1")
    assert completed_job.status == "COMPLETED"
    assert completed_job.progress == 1.0
    assert completed_job.result["score"] == 99


def test_research_job_runner_failure_handling(tmp_path):
    jobs_file = str(tmp_path / "jobs.json")
    store = JobStore(path=jobs_file)
    runner = ResearchJobRunner(store=store)

    def failing_worker(s, j_id):
        raise RuntimeError("Simulated worker exception")

    runner.submit_and_execute_async("job_fail_1", "ANALYTICS", failing_worker)
    time.sleep(0.3)

    failed_job = store.get("job_fail_1")
    assert failed_job.status == "FAILED"
    assert "Simulated worker exception" in failed_job.error


# ==============================================================================
# 3. RUNTIME LEASE & SUPERVISOR
# ==============================================================================

def test_runtime_lease_and_heartbeat():
    lease = RuntimeLease(runtime_id="rt_1", strategy_id="adx_ema", lease_seconds=10)
    hb = lease.acquire()

    assert hb.runtime_id == "rt_1"
    assert hb.status == "RUNNING"
    assert lease.is_valid()

    hb2 = lease.heartbeat("PAUSED")
    assert hb2.status == "PAUSED"
    assert lease.is_valid()


def test_runtime_supervisor_decisions(tmp_path):
    leases_file = str(tmp_path / "leases.json")
    sup = RuntimeSupervisor(leases_path=leases_file)

    # 1. No heartbeat
    ok, reason = sup.evaluate(None)
    assert not ok
    assert reason == "NO_HEARTBEAT"

    # 2. Active, valid heartbeat
    now = datetime.now(timezone.utc)
    future = now + timedelta(seconds=30)
    hb_valid = RuntimeHeartbeat(
        runtime_id="rt_1",
        strategy_id="strat",
        status="RUNNING",
        timestamp=now.isoformat(),
        lease_expires_at=future.isoformat(),
    )
    ok, reason = sup.evaluate(hb_valid)
    assert ok
    assert reason == "RUNTIME_OK"

    # 3. Expired lease
    past = now - timedelta(seconds=5)
    hb_expired = RuntimeHeartbeat(
        runtime_id="rt_1",
        strategy_id="strat",
        status="RUNNING",
        timestamp=past.isoformat(),
        lease_expires_at=past.isoformat(),
    )
    ok, reason = sup.evaluate(hb_expired)
    assert not ok
    assert reason == "LEASE_EXPIRED"

    # 4. Unhealthy status
    hb_failed = RuntimeHeartbeat(
        runtime_id="rt_1",
        strategy_id="strat",
        status="FAILED",
        timestamp=now.isoformat(),
        lease_expires_at=future.isoformat(),
    )
    ok, reason = sup.evaluate(hb_failed)
    assert not ok
    assert "RUNTIME_NOT_HEALTHY" in reason


# ==============================================================================
# 4. EXECUTION INTENTS & IDEMPOTENCY
# ==============================================================================

def test_execution_intent_contract():
    intent = ExecutionIntent(
        intent_id="intent_sig_123",
        strategy_id="adx_ema",
        strategy_version="v1.0.0",
        symbol="BTCUSDT",
        side="BUY",
        quantity=0.015,
        order_type="MARKET",
        price=60000.0,
        paper_only=True,
        metadata={"timeframe": "15m"}
    )
    assert intent.intent_id == "intent_sig_123"
    assert intent.quantity == 0.015
    assert intent.paper_only is True


def test_idempotency_guard_prevents_duplicate_orders(tmp_path):
    intent_file = str(tmp_path / "intents.json")
    guard = IdempotencyGuard(path=intent_file)

    assert not guard.seen("intent_001")

    # Record first submission
    first_res = guard.record("intent_001", exchange_order_id="123456", status="FILLED")
    assert first_res is True
    assert guard.seen("intent_001")

    # Attempt duplicate recording of identical intent
    dup_res = guard.record("intent_001", exchange_order_id="999999", status="FILLED")
    assert dup_res is False

    # Stored order ID remains original
    record = guard.get_intent("intent_001")
    assert record["exchange_order_id"] == "123456"


# ==============================================================================
# 5. RESEARCH AGENT GATEWAY
# ==============================================================================

def test_research_agent_gateway_isolation(tmp_path):
    store = JobStore(path=str(tmp_path / "jobs.json"))
    registry = StrategyRegistry(path=str(tmp_path / "registry.json"))
    gateway = ResearchAgentGateway(store=store, registry=registry)

    # Agent submits backtest
    res = gateway.submit_backtest("agent_job_1", "adx_ema", parameters={"adx_threshold": 20})
    assert res["job_id"] == "agent_job_1"
    assert res["job_type"] == "BACKTEST"

    # Agent inspects status
    info = gateway.get_job("agent_job_1")
    assert info["status"] == "QUEUED"

    # Verification: Gateway has zero order execution or trading methods
    gateway_methods = [m for m in dir(gateway) if not m.startswith("_")]
    for m in gateway_methods:
        assert "order" not in m.lower(), f"Gateway leaked trading method: {m}"
        assert "buy" not in m.lower(), f"Gateway leaked trading method: {m}"
        assert "sell" not in m.lower(), f"Gateway leaked trading method: {m}"
        assert "trade" not in m.lower(), f"Gateway leaked trading method: {m}"


# ==============================================================================
# 6. SAFETY INVARIANTS
# ==============================================================================

def test_safety_invariants_live_and_paper(monkeypatch):
    # 1. ExecutionPolicy strictly forbids LIVE trading
    monkeypatch.setattr("execution.TRADING_MODE", "LIVE")
    monkeypatch.setattr("execution.LIVE_TRADING_ENABLED", True)
    can_place, reason = ExecutionPolicy.can_place_order()
    assert not can_place
    assert "LIVE_FORBIDDEN" in reason

    # 2. PAPER mode is blocked from placing external exchange orders
    monkeypatch.setattr("execution.TRADING_MODE", "PAPER")
    monkeypatch.setattr("execution.LIVE_TRADING_ENABLED", False)
    can_place_paper, paper_reason = ExecutionPolicy.can_place_order()
    assert not can_place_paper
    assert "PAPER_BLOCKED" in paper_reason

