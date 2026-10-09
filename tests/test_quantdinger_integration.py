"""Tests for QuantDinger architectural integration into STRATEX."""

import json
import pytest
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from concurrent.futures import ProcessPoolExecutor
import multiprocessing

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


def _register_validation_candidate(registry, root, strategy_id, version, source, parameters):
    """Writes explicitly synthetic evidence to a temp path for gate tests only."""
    from stratex_quantdinger.promotion_policy import PROMOTION_ELIGIBLE

    artifact_name = f"{strategy_id}_{version}_synthetic_test_evidence.json"
    artifact = {
        "strategy": strategy_id,
        "strategy_source_hash": StrategyRegistry.compute_source_hash(source),
        "git_sha": "a" * 40,
        "promotion_status": PROMOTION_ELIGIBLE,
        "optimized_oos": {
            "trade_count": 40,
            "total_trades": 40,
            "profit_factor": 1.2,
            "expectancy": 0.5,
        },
        "walk_forward_windows": [{"status": "PASS"}, {"status": "PASS"}],
    }
    (Path(root) / artifact_name).write_text(json.dumps(artifact), encoding="utf-8")
    return registry.register(
        strategy_id,
        version,
        source,
        parameters,
        evidence={
            "artifact": artifact_name,
            "git_sha": "a" * 40,
            "promotion_status": PROMOTION_ELIGIBLE,
            "walk_forward_failures": 0,
        },
    )


def _parallel_registry_register(args):
    registry_path, index = args
    StrategyRegistry(path=registry_path, audit_log_path=registry_path + ".audit").register(
        f"parallel_{index}", "v1", f"source-{index}", {"index": index}
    )
    return index


def _parallel_registry_promote(args):
    registry_path, version = args
    return StrategyRegistry(path=registry_path, audit_log_path=registry_path + ".audit").promote(
        "race_strategy", version, "ACTIVE", actor="operator", reason="concurrency regression test"
    ).status


# ==============================================================================
# 1. STRATEGY REGISTRY & IMMUTABLE VERSIONING
# ==============================================================================

def test_factory_winner_5_registry_hash_matches_committed_source():
    root = Path(__file__).resolve().parent.parent
    registry_data = json.loads((root / "strategy_registry.json").read_text(encoding="utf-8"))
    source = (root / "strategy_factory_winner_5.py").read_text(encoding="utf-8")
    expected = StrategyRegistry.compute_source_hash(source)
    record = registry_data["strategies"]["factory_winner_5"]["v1.0.0"]

    assert len(record["source_hash"]) == 64
    assert record["source_hash"] == expected
    assert record["status"] == "RESEARCH"  # Hash correction is not validation.


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

    _register_validation_candidate(registry, tmp_path, "trend", "v1.0.0", "source", {"sl": 2.0})

    # Cannot jump directly from RESEARCH to ACTIVE
    with pytest.raises(ValueError, match="Illegal lifecycle transition"):
        registry.promote("trend", "v1.0.0", "ACTIVE")

    # Step 1: RESEARCH -> OOS_VALIDATED (synthetic artifact is test-only)
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


def test_corrupt_registry_is_not_silently_reset_or_overwritten(tmp_path):
    from stratex_quantdinger.registry import RegistryIntegrityError

    registry_path = tmp_path / "registry.json"
    original = '{"strategies": {broken'
    registry_path.write_text(original, encoding="utf-8")
    registry = StrategyRegistry(path=str(registry_path))

    with pytest.raises(RegistryIntegrityError, match="refusing to reset"):
        registry.register("new", "v1", "source", {})

    assert registry_path.read_text(encoding="utf-8") == original


def test_corrupt_registry_endpoint_returns_503_without_overwriting(tmp_path, monkeypatch):
    from dashboard import app

    registry_path = tmp_path / "strategy_registry.json"
    original = "not-json"
    registry_path.write_text(original, encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    for key in ("BOT_API_KEY", "API_KEY_READONLY", "API_KEY_FRIDAY"):
        monkeypatch.setenv(key, "")
    monkeypatch.setenv("API_KEY_CONTROL", "registry-control-test-key-123456")

    with app.test_client() as client:
        response = client.get("/api/strategy-registry")

    assert response.status_code == 503
    assert response.get_json()["error"] == "REGISTRY_UNAVAILABLE"
    assert registry_path.read_text(encoding="utf-8") == original


def test_parallel_registry_writes_are_serialized_across_processes(tmp_path):
    if "fork" not in multiprocessing.get_all_start_methods():
        pytest.skip("cross-process flock stress test requires the POSIX fork start method")

    registry_path = str(tmp_path / "registry.json")
    context = multiprocessing.get_context("fork")
    with ProcessPoolExecutor(max_workers=8, mp_context=context) as pool:
        list(pool.map(_parallel_registry_register, [(registry_path, index) for index in range(48)]))

    registry = StrategyRegistry(path=registry_path, audit_log_path=registry_path + ".audit")
    assert len(registry.list_versions()) == 48
    assert len(Path(registry_path + ".audit").read_text(encoding="utf-8").splitlines()) == 48


def test_concurrent_active_promotions_leave_exactly_one_active_version(tmp_path):
    if "fork" not in multiprocessing.get_all_start_methods():
        pytest.skip("cross-process flock stress test requires the POSIX fork start method")

    registry_path = str(tmp_path / "registry.json")
    registry = StrategyRegistry(path=registry_path, audit_log_path=registry_path + ".audit")
    for version in ("v1", "v2"):
        _register_validation_candidate(registry, tmp_path, "race_strategy", version, f"source-{version}", {})
        registry.promote("race_strategy", version, "OOS_VALIDATED")
        registry.promote("race_strategy", version, "APPROVED")

    context = multiprocessing.get_context("fork")
    with ProcessPoolExecutor(max_workers=2, mp_context=context) as pool:
        list(pool.map(_parallel_registry_promote, [(registry_path, "v1"), (registry_path, "v2")]))

    versions = registry.list_versions(strategy_id="race_strategy")
    assert sum(item.status == "ACTIVE" for item in versions) == 1
    assert sum(item.status == "RETIRED" for item in versions) == 1


def test_legacy_short_hash_is_readable_only_while_unpromoted(tmp_path):
    from stratex_quantdinger.registry import RegistryIntegrityError

    registry_path = tmp_path / "registry.json"
    record = {
        "strategy_id": "legacy",
        "version": "v1",
        "source_hash": "a" * 61,
        "created_at": "2026-09-01T00:00:00+00:00",
        "parameters": {},
        "status": "RESEARCH",
    }
    registry_path.write_text(json.dumps({"strategies": {"legacy": {"v1": record}}}), encoding="utf-8")
    registry = StrategyRegistry(path=str(registry_path))
    assert registry.get("legacy", "v1").status == "RESEARCH"

    record["status"] = "OOS_VALIDATED"
    registry_path.write_text(json.dumps({"strategies": {"legacy": {"v1": record}}}), encoding="utf-8")
    with pytest.raises(RegistryIntegrityError, match="full SHA-256"):
        registry.list_versions()


def test_registry_mutation_endpoints_reject_non_object_json(tmp_path, monkeypatch):
    from dashboard import app

    monkeypatch.chdir(tmp_path)
    for key in ("BOT_API_KEY", "API_KEY_READONLY", "API_KEY_FRIDAY"):
        monkeypatch.setenv(key, "")
    api_key = "registry-json-shape-test-key-123456"
    monkeypatch.setenv("API_KEY_CONTROL", api_key)

    with app.test_client() as client:
        registration = client.post("/api/strategy-registry", json=["not", "an", "object"], headers={"X-API-Key": api_key})
        promotion = client.post("/api/strategy-registry/promote", json=["not", "an", "object"], headers={"X-API-Key": api_key})

    assert registration.status_code == 400
    assert registration.get_json()["error"] == "JSON_OBJECT_REQUIRED"
    assert promotion.status_code == 400
    assert promotion.get_json()["error"] == "JSON_OBJECT_REQUIRED"


def test_registry_rejects_direct_elevated_registration(tmp_path):
    registry = StrategyRegistry(path=str(tmp_path / "registry.json"))
    with pytest.raises(ValueError, match="must be registered as RESEARCH"):
        registry.register("strat", "v1.0.0", "source", {}, status="ACTIVE")


def test_oos_promotion_rejects_missing_artifact(tmp_path):
    registry = StrategyRegistry(path=str(tmp_path / "registry.json"))
    registry.register("strat", "v1.0.0", "source", {})
    with pytest.raises(ValueError, match="requires evidence.artifact"):
        registry.promote("strat", "v1.0.0", "OOS_VALIDATED")


def test_oos_promotion_rejects_research_only_and_underpowered_artifact(tmp_path):
    registry = StrategyRegistry(path=str(tmp_path / "registry.json"))
    artifact_name = "weak.json"
    artifact = {
        "strategy": "strat",
        "strategy_source_hash": StrategyRegistry.compute_source_hash("source"),
        "promotion_status": "RESEARCH ONLY",
        "optimized_oos": {"trade_count": 2, "profit_factor": 0.8236},
        "walk_forward_windows": [{"status": "FAIL"}],
    }
    (tmp_path / artifact_name).write_text(json.dumps(artifact), encoding="utf-8")
    registry.register(
        "strat", "v1.0.0", "source", {},
        evidence={"artifact": artifact_name, "promotion_status": "RESEARCH ONLY"},
    )
    with pytest.raises(ValueError, match="evidence is insufficient"):
        registry.promote("strat", "v1.0.0", "OOS_VALIDATED")


def test_oos_promotion_rejects_artifact_path_escape(tmp_path):
    registry = StrategyRegistry(path=str(tmp_path / "registry.json"))
    registry.register("strat", "v1.0.0", "source", {}, evidence={"artifact": "../outside.json"})
    with pytest.raises(ValueError, match="relative path inside"):
        registry.promote("strat", "v1.0.0", "OOS_VALIDATED")


def test_oos_promotion_rejects_symlink_escape(tmp_path):
    outside = tmp_path.parent / f"{tmp_path.name}_outside.json"
    outside.write_text("{}", encoding="utf-8")
    link = tmp_path / "outside_link.json"
    try:
        link.symlink_to(outside)
    except OSError as exc:
        pytest.skip(f"symlink creation unavailable: {exc}")

    registry = StrategyRegistry(path=str(tmp_path / "registry.json"))
    registry.register("strat", "v1.0.0", "source", {}, evidence={"artifact": link.name})
    with pytest.raises(ValueError, match="missing or outside"):
        registry.promote("strat", "v1.0.0", "OOS_VALIDATED")


def test_oos_evidence_is_pinned_and_cannot_be_changed_before_approval(tmp_path):
    registry = StrategyRegistry(path=str(tmp_path / "registry.json"))
    _register_validation_candidate(registry, tmp_path, "strat", "v1.0.0", "source", {})
    registry.promote("strat", "v1.0.0", "OOS_VALIDATED")

    artifact_path = tmp_path / "strat_v1.0.0_synthetic_test_evidence.json"
    artifact = json.loads(artifact_path.read_text(encoding="utf-8"))
    artifact["optimized_oos"]["expectancy"] = 0.6
    artifact_path.write_text(json.dumps(artifact), encoding="utf-8")

    with pytest.raises(ValueError, match="changed after its evidence hash"):
        registry.promote("strat", "v1.0.0", "APPROVED")


def test_oos_promotion_rejects_artifact_for_different_source_version(tmp_path):
    from stratex_quantdinger.promotion_policy import PROMOTION_ELIGIBLE

    registry = StrategyRegistry(path=str(tmp_path / "registry.json"))
    artifact_name = "other_source.json"
    artifact = {
        "strategy": "strat",
        "strategy_source_hash": StrategyRegistry.compute_source_hash("different source"),
        "promotion_status": PROMOTION_ELIGIBLE,
        "optimized_oos": {"trade_count": 40, "profit_factor": 1.2},
        "walk_forward_windows": [{"status": "PASS"}],
    }
    (tmp_path / artifact_name).write_text(json.dumps(artifact), encoding="utf-8")
    registry.register("strat", "v1.0.0", "actual source", {}, evidence={"artifact": artifact_name})

    with pytest.raises(ValueError, match="source hash does not match"):
        registry.promote("strat", "v1.0.0", "OOS_VALIDATED")


def test_oos_promotion_rejects_registry_metrics_that_disagree_with_artifact(tmp_path):
    registry = StrategyRegistry(path=str(tmp_path / "registry.json"))
    artifact_name = "valid_metrics.json"
    artifact = {
        "strategy": "strat",
        "strategy_source_hash": StrategyRegistry.compute_source_hash("source"),
        "git_sha": "a" * 40,
        "promotion_status": "PROMOTION_ELIGIBLE",
        "optimized_oos": {"trade_count": 40, "profit_factor": 1.2, "expectancy": 0.5},
        "walk_forward_windows": [{"status": "PASS"}],
    }
    (tmp_path / artifact_name).write_text(json.dumps(artifact), encoding="utf-8")
    registry.register(
        "strat", "v1.0.0", "source",
        {"OOS_TRADE_COUNT": 2, "OOS_PROFIT_FACTOR": 0.8236},
        evidence={
            "artifact": artifact_name,
            "git_sha": "a" * 40,
            "promotion_status": "PROMOTION_ELIGIBLE",
            "walk_forward_failures": 0,
        },
    )
    with pytest.raises(ValueError, match="does not match the cited artifact"):
        registry.promote("strat", "v1.0.0", "OOS_VALIDATED")


def test_strategy_single_active_version_rule(tmp_path):
    reg_file = str(tmp_path / "registry.json")
    registry = StrategyRegistry(path=reg_file)

    _register_validation_candidate(registry, tmp_path, "strat", "v1.0.0", "source 1", {})
    registry.promote("strat", "v1.0.0", "OOS_VALIDATED")
    registry.promote("strat", "v1.0.0", "APPROVED")
    registry.promote("strat", "v1.0.0", "ACTIVE")

    assert registry.get_active("strat").version == "v1.0.0"

    # Register and activate v2.0.0
    _register_validation_candidate(registry, tmp_path, "strat", "v2.0.0", "source 2", {})
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
    # Pin config and execution-module bindings: _resolve_execution_flags ORs
    # both sources, so mutating only one makes this test order-dependent.
    import config
    import execution

    monkeypatch.setattr(config, "TRADING_MODE", "LIVE")
    monkeypatch.setattr(config, "PAPER_SAFE_MODE", False)
    monkeypatch.setattr(config, "LIVE_TRADING_ENABLED", False)
    monkeypatch.setattr(execution, "TRADING_MODE", "LIVE")
    monkeypatch.setattr(execution, "PAPER_SAFE_MODE", False)
    monkeypatch.setattr(execution, "LIVE_TRADING_ENABLED", True)
    can_place, reason = ExecutionPolicy.can_place_order()
    assert not can_place
    assert "LIVE_FORBIDDEN" in reason

    # 2. PAPER mode is blocked from placing external exchange orders
    monkeypatch.setattr(config, "TRADING_MODE", "PAPER")
    monkeypatch.setattr(config, "PAPER_SAFE_MODE", False)
    monkeypatch.setattr(config, "LIVE_TRADING_ENABLED", False)
    monkeypatch.setattr(execution, "TRADING_MODE", "PAPER")
    monkeypatch.setattr(execution, "PAPER_SAFE_MODE", False)
    monkeypatch.setattr(execution, "LIVE_TRADING_ENABLED", False)
    can_place_paper, paper_reason = ExecutionPolicy.can_place_order()
    assert not can_place_paper
    assert "PAPER_BLOCKED" in paper_reason

