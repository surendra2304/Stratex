"""Item 8 regression tests — persistence robustness.

Corrupt / truncated / wrong-type / concurrent JSON and JSONL state must be
detected, preserved (quarantined or left untouched) and handled fail-closed;
it must never be silently replaced by an empty default, glued onto a torn
line, or raced by a second writer.
"""

from __future__ import annotations

import datetime
import glob
import json
import multiprocessing
import os

import pytest

import atomic_io
from atomic_io import StateFileError, dataclass_from_mapping, load_json_document, publish_new_file
from audit import audit_manager as am
from audit.audit_manager import AuditManager, AuditWriteError, IdempotencyStore, persisted_hash


def _lines(path):
    with open(path, encoding="utf-8") as handle:
        return [line for line in handle.read().split("\n") if line.strip()]


def _corrupt_siblings(path, marker=".corrupt-"):
    return glob.glob(str(path) + marker + "*")


# ── AuditManager ────────────────────────────────────────────────────────────

def test_two_instances_extend_one_chain(tmp_path):
    log = str(tmp_path / "audit.jsonl")
    first, second = AuditManager(log), AuditManager(log)
    a = first.record_event("E1", "a", {"n": 1})
    b = second.record_event("E2", "b", {"n": 2})
    c = first.record_event("E3", "a", {"n": 3})
    assert b["prev_hash"] == a["hash"]
    assert c["prev_hash"] == b["hash"], "instance must chain to the on-disk head, not its own cache"
    assert first.verify_log_integrity() == (True, 3, "Integrity verified. Chain valid.")


def _audit_worker(log, worker, count):
    manager = AuditManager(log)
    for index in range(count):
        manager.record_event("STRESS", f"w{worker}", {"i": index})


def test_concurrent_processes_keep_the_chain_valid(tmp_path):
    log = str(tmp_path / "audit.jsonl")
    ctx = multiprocessing.get_context("fork")
    procs = [ctx.Process(target=_audit_worker, args=(log, w, 25)) for w in range(4)]
    for proc in procs:
        proc.start()
    for proc in procs:
        proc.join(60)
        assert proc.exitcode == 0
    valid, count, detail = AuditManager(log).verify_log_integrity()
    assert valid, detail
    assert count == 100


def test_failed_write_is_reported_and_does_not_advance_head(tmp_path, monkeypatch):
    log = str(tmp_path / "audit.jsonl")
    manager = AuditManager(log)
    first = manager.record_event("OK", "t", {})
    real_open = open

    def failing_open(path, mode="r", *args, **kwargs):
        if str(path) == log and "a" in mode:
            raise OSError("disk full")
        return real_open(path, mode, *args, **kwargs)

    monkeypatch.setattr(am, "open", failing_open, raising=False)
    failed = manager.record_event("LOST", "t", {})
    assert failed["persisted"] is False and "disk full" in failed["persist_error"]
    assert persisted_hash(failed) is None
    assert manager.head_hash == first["hash"]
    assert manager.write_failures == 1
    with pytest.raises(AuditWriteError):
        manager.record_event("REQUIRED", "t", {}, required=True)
    monkeypatch.undo()

    after = manager.record_event("OK2", "t", {})
    assert after["prev_hash"] == first["hash"]
    assert persisted_hash(after) == after["hash"]
    assert manager.verify_log_integrity()[:2] == (True, 2)


def test_torn_tail_is_reported_preserved_and_repaired(tmp_path):
    log = str(tmp_path / "audit.jsonl")
    manager = AuditManager(log)
    manager.record_event("E1", "t", {})
    second = manager.record_event("E2", "t", {})
    with open(log, "a", encoding="utf-8") as handle:
        handle.write('{"timestamp": "2026-10-09T00:00')  # crash mid-append
    valid, count, detail = manager.verify_log_integrity()
    assert not valid and count == 2 and "Torn final line 3" in detail

    third = manager.record_event("E3", "t", {})
    assert third["prev_hash"] == second["hash"]
    side = _corrupt_siblings(log, ".corrupt-torn-")
    assert len(side) == 1
    with open(side[0], encoding="utf-8") as handle:
        assert handle.read() == '{"timestamp": "2026-10-09T00:00'
    assert manager.torn_tails_repaired == 1
    assert manager.verify_log_integrity()[:2] == (True, 3)


def test_complete_last_record_without_newline_is_terminated(tmp_path):
    log = str(tmp_path / "audit.jsonl")
    manager = AuditManager(log)
    first = manager.record_event("E1", "t", {})
    with open(log, "rb+") as handle:
        handle.seek(-1, os.SEEK_END)
        handle.truncate()
    second = AuditManager(log).record_event("E2", "t", {})
    assert second["prev_hash"] == first["hash"]
    assert len(_lines(log)) == 2
    assert manager.verify_log_integrity()[:2] == (True, 2)


def test_log_without_any_valid_record_is_quarantined(tmp_path):
    log = tmp_path / "audit.jsonl"
    log.write_text("garbage line\nmore garbage\n", encoding="utf-8")
    manager = AuditManager(str(log))
    event = manager.record_event("E1", "t", {})
    assert event["prev_hash"] == am.GENESIS_HASH
    assert len(_corrupt_siblings(log)) == 1
    assert manager.verify_log_integrity()[:2] == (True, 1)


def test_corrupt_middle_line_is_flagged(tmp_path):
    log = str(tmp_path / "audit.jsonl")
    manager = AuditManager(log)
    for name in ("E1", "E2"):
        manager.record_event(name, "t", {})
    lines = _lines(log)
    with open(log, "w", encoding="utf-8") as handle:
        handle.write(lines[0] + "\n{broken\n" + lines[1] + "\n")
    valid, count, detail = manager.verify_log_integrity()
    assert not valid and count == 1 and detail.startswith("Corrupt record at line 2")


def test_non_finite_details_are_stored_as_null(tmp_path):
    log = str(tmp_path / "audit.jsonl")
    manager = AuditManager(log)
    event = manager.record_event("E", "t", {"pnl": float("nan"), "nested": [float("inf"), 1.5], "when": datetime.date(2026, 1, 2)})
    assert event["details"] == {"pnl": None, "nested": [None, 1.5], "when": "2026-01-02"}
    atomic_io.parse_json_strict(_lines(log)[0])  # strict JSON on disk
    assert manager.verify_log_integrity()[:2] == (True, 1)


def test_head_recovery_scans_only_the_tail_of_a_large_log(tmp_path):
    log = str(tmp_path / "audit.jsonl")
    manager = AuditManager(log)
    last = None
    for index in range(300):
        last = manager.record_event("BULK", "t", {"i": index, "pad": "x" * 400})
    assert os.path.getsize(log) > 2 * 64 * 1024  # spans several tail chunks
    assert AuditManager(log).head_hash == last["hash"]


# ── IdempotencyStore ────────────────────────────────────────────────────────

def test_corrupt_store_is_quarantined_not_silently_emptied(tmp_path):
    path = tmp_path / "store.json"
    path.write_text("{not json", encoding="utf-8")
    store = IdempotencyStore(str(path))
    assert store.load_status == "corrupt"
    assert store.quarantined_to and os.path.exists(store.quarantined_to)
    assert store.check_and_record("k1", "ORDER") == (False, store.get("k1"))


def test_list_typed_store_no_longer_crashes(tmp_path):
    path = tmp_path / "store.json"
    path.write_text("[1, 2, 3]", encoding="utf-8")
    store = IdempotencyStore(str(path))
    is_dup, record = store.check_and_record("k1", "ORDER")
    assert is_dup is False and record["status"] == "PENDING"
    assert len(_corrupt_siblings(path)) == 1


def test_malformed_records_are_dropped_valid_ones_kept(tmp_path):
    path = tmp_path / "store.json"
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    path.write_text(json.dumps({"good": {"status": "COMPLETED", "completed_at": now, "response": {"id": 1}},
                                "bad": [1, 2]}), encoding="utf-8")
    store = IdempotencyStore(str(path))
    assert store.invalid_entries_dropped == 1
    is_dup, cached = store.check_and_record("good", "ORDER")
    assert is_dup and cached["response"] == {"id": 1}


def test_second_instance_sees_records_of_the_first(tmp_path):
    path = str(tmp_path / "store.json")
    first, second = IdempotencyStore(path), IdempotencyStore(path)
    assert first.check_and_record("order-1", "ORDER")[0] is False
    is_dup, cached = second.check_and_record("order-1", "ORDER")
    assert is_dup and cached["status"] == "PENDING"
    first.complete_request("order-1", {"orderId": 7})
    assert second.check_and_record("order-1", "ORDER")[1]["response"] == {"orderId": 7}


def _idempotency_worker(path, queue):
    queue.put(IdempotencyStore(path).check_and_record("same-key", "ORDER")[0])


def test_exactly_one_process_wins_a_key(tmp_path):
    path = str(tmp_path / "store.json")
    ctx = multiprocessing.get_context("fork")
    queue = ctx.Queue()
    procs = [ctx.Process(target=_idempotency_worker, args=(path, queue)) for _ in range(8)]
    for proc in procs:
        proc.start()
    for proc in procs:
        proc.join(60)
    results = [queue.get(timeout=10) for _ in procs]
    assert results.count(False) == 1, results


def test_old_records_are_pruned_and_size_is_capped(tmp_path):
    path = tmp_path / "store.json"
    old = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=45)).isoformat()
    path.write_text(json.dumps({"old": {"status": "COMPLETED", "completed_at": old},
                                "undated": {"status": "COMPLETED"}}), encoding="utf-8")
    store = IdempotencyStore(str(path), retention_days=30, max_entries=3)
    for index in range(5):
        store.complete_request(f"k{index}", {"i": index})
    with open(path, encoding="utf-8") as handle:
        on_disk = json.load(handle)
    assert "old" not in on_disk and "undated" not in on_disk
    assert set(on_disk) == {"k2", "k3", "k4"}
    assert store.pruned_total >= 4


def test_remove_keeps_completed_records_unless_forced(tmp_path):
    store = IdempotencyStore(str(tmp_path / "store.json"))
    store.check_and_record("done", "ORDER")
    store.complete_request("done", {"ok": True})
    assert store.remove("done") is False
    assert store.get("done")["status"] == "COMPLETED"
    assert store.remove("done", force=True) is True
    store.check_and_record("pending", "ORDER")
    assert store.remove("pending") is True


def test_non_finite_response_does_not_poison_the_store(tmp_path):
    path = str(tmp_path / "store.json")
    store = IdempotencyStore(path)
    store.complete_request("k", {"avgPrice": float("nan"), "fills": [float("inf")]})
    reloaded = IdempotencyStore(path)
    assert reloaded.load_status == "ok"
    assert reloaded.get("k")["response"] == {"avgPrice": None, "fills": [None]}


def test_persist_failure_keeps_dedup_in_memory_and_recovers(tmp_path, monkeypatch):
    path = str(tmp_path / "store.json")
    store = IdempotencyStore(path)

    def boom(*args, **kwargs):
        raise OSError("read-only filesystem")

    monkeypatch.setattr(am, "atomic_write_json", boom)
    assert store.check_and_record("k", "ORDER")[0] is False
    assert store.persist_failures == 1
    assert store.check_and_record("k", "ORDER")[0] is True  # still deduplicated
    monkeypatch.undo()
    store.complete_request("k", {"ok": 1})
    assert IdempotencyStore(path).get("k")["status"] == "COMPLETED"
    assert store.status()["unpersisted_changes"] == 0


def test_stale_pending_record_expires(tmp_path):
    path = tmp_path / "store.json"
    created = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(seconds=120)).isoformat()
    path.write_text(json.dumps({"k": {"status": "PENDING", "created_at": created}}), encoding="utf-8")
    store = IdempotencyStore(str(path))
    assert store.check_and_record("k", "ORDER")[0] is False


# ── advisory parameter overlay ──────────────────────────────────────────────

def _overlay(tmp_path):
    from advisory_params import AdvisoryParameterOverlay

    return AdvisoryParameterOverlay(state_file=str(tmp_path / "advisory.json"))


def test_corrupt_overlay_is_quarantined_and_defaults_apply(tmp_path):
    (tmp_path / "advisory.json").write_text('{"overrides": {"adx_ema": {"risk": 0.5}', encoding="utf-8")
    overlay = _overlay(tmp_path)
    assert overlay.state_status == "corrupt" and os.path.exists(overlay.quarantined_to)
    assert overlay.get_param("adx_ema", "risk", default=0.01) == 0.01


def test_structurally_invalid_overlay_is_quarantined(tmp_path):
    (tmp_path / "advisory.json").write_text(json.dumps({"overrides": ["risk", 0.5]}), encoding="utf-8")
    overlay = _overlay(tmp_path)
    assert overlay.state_status == "corrupt"
    assert len(_corrupt_siblings(tmp_path / "advisory.json")) == 1


def test_unpersisted_change_is_rolled_back(tmp_path, monkeypatch):
    import advisory_params

    overlay = _overlay(tmp_path)

    def boom(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(advisory_params, "atomic_write_json", boom)
    change = {"strategy": "adx_ema", "parameter": "risk", "new_value": 0.05, "current_value": 0.01}
    assert overlay.apply_changes("dec-1", [change]) is False
    assert overlay.get_param("adx_ema", "risk", default=0.01) == 0.01
    assert overlay._history == []


def test_non_finite_parameter_is_never_applied(tmp_path):
    overlay = _overlay(tmp_path)
    change = {"strategy": "adx_ema", "parameter": "risk", "new_value": float("nan"), "current_value": 0.01}
    assert overlay.apply_changes("dec-nan", [change]) is False
    assert overlay.get_param("adx_ema", "risk", default=0.01) == 0.01


def _stage(overlay, **extra):
    rec = {"recommendation_id": "rec-1", "strategy": "adx_ema", "parameter": "risk", "proposed_value": 0.02,
           "current_value": 0.01}
    rec.update(extra)
    return overlay.stage_recommendation(rec)


def test_authorization_requires_a_persisted_audit_record(tmp_path, monkeypatch):
    overlay = _overlay(tmp_path)
    _stage(overlay)

    def no_audit(*args, **kwargs):
        if kwargs.get("required"):
            raise AuditWriteError("audit disk full")
        return {"hash": "0" * 64}

    monkeypatch.setattr(am.get_audit_manager(), "record_event", no_audit)
    ok, message, _ = overlay.apply_authorized_recommendation("rec-1", "token-123456789", idempotency_key="idem-1")
    assert ok is False and "Audit trail unavailable" in message
    assert overlay.get_param("adx_ema", "risk", default=0.01) == 0.01
    # The failed attempt released its key: a corrected retry is not a "duplicate".
    assert am.get_idempotency_store().get("idem-1") is None
    monkeypatch.undo()
    ok, _, payload = overlay.apply_authorized_recommendation("rec-1", "token-123456789", idempotency_key="idem-1")
    assert ok is True and payload["audit_event"]
    assert overlay.get_param("adx_ema", "risk") == 0.02


def test_unparseable_expiry_fails_closed(tmp_path):
    overlay = _overlay(tmp_path)
    _stage(overlay, expiry="next tuesday")
    ok, message, _ = overlay.apply_authorized_recommendation("rec-1", "token-123456789")
    assert ok is False and "unparseable expiry" in message
    assert overlay.get_param("adx_ema", "risk", default=0.01) == 0.01


# ── paper engine state files ────────────────────────────────────────────────

def test_alert_state_corruption_is_quarantined(tmp_path):
    from paper_engine.alerts import AlertManager

    path = tmp_path / "alerts.json"
    path.write_text('{"active": [', encoding="utf-8")
    alerts = AlertManager(str(path))
    assert alerts.load_status == "corrupt" and alerts.quarantined_to
    alerts.raise_alert("DD", "HIGH", "drawdown", "acct")
    assert json.loads(path.read_text(encoding="utf-8"))["active"]["DD_acct"]["count"] == 1


def test_alert_state_with_wrong_shape_is_quarantined(tmp_path):
    from paper_engine.alerts import AlertManager

    path = tmp_path / "alerts.json"
    path.write_text(json.dumps({"active": ["x"], "historical": {}}), encoding="utf-8")
    assert AlertManager(str(path)).load_status == "corrupt"
    assert len(_corrupt_siblings(path)) == 1


def test_alert_history_is_bounded(tmp_path, monkeypatch):
    from paper_engine import alerts as alerts_module

    monkeypatch.setattr(alerts_module, "HISTORY_LIMIT", 5)
    manager = alerts_module.AlertManager(str(tmp_path / "alerts.json"))
    for index in range(12):
        manager.raise_alert("T", "LOW", "m", str(index))
        manager.resolve_alert("T", str(index))
    assert len(json.loads((tmp_path / "alerts.json").read_text())["historical"]) == 5


@pytest.mark.parametrize("payload", ["[1, 2]", '{"status": "DANCING"}', '{"start_time": NaN}', "{trunc"])
def test_session_state_corruption_fails_closed(tmp_path, payload):
    from paper_engine.exceptions import PersistenceError
    from paper_engine.session import SessionState

    path = tmp_path / "session.json"
    path.write_text(payload, encoding="utf-8")
    with pytest.raises(PersistenceError):
        SessionState(str(path))
    assert path.read_text(encoding="utf-8") == payload  # evidence untouched


def test_session_state_round_trip_includes_config_snapshot(tmp_path):
    from paper_engine.session import SessionState

    path = str(tmp_path / "session.json")
    session = SessionState(path)
    session.start_session({"symbols": ["BTCUSDT"]})
    reloaded = SessionState(path)
    assert reloaded.status == "RUNNING" and reloaded.config_snapshot == {"symbols": ["BTCUSDT"]}


def test_paper_signal_dataset_is_never_overwritten_when_corrupt(tmp_path):
    from paper_engine.signal_logger import PaperSignalLogger, Signal

    path = tmp_path / "signals.json"
    path.write_text('[{"signal_id": "old"', encoding="utf-8")
    logger = PaperSignalLogger(str(path))
    assert logger.load_status == "corrupt"
    with open(logger.quarantined_to, encoding="utf-8") as handle:
        assert handle.read() == '[{"signal_id": "old"'
    signal = Signal("BTCUSDT", "4h", "LONG", 0.6, 1.0, float("nan"), "adx", "ENTRY", "test")
    ids = {logger.log_signal(signal) for _ in range(5)}
    assert len(ids) == 5, "ids must be unique within one millisecond"
    stored = json.loads(path.read_text(encoding="utf-8"))
    assert stored[0]["reference_price"] is None
    assert logger.update_outcome(stored[2]["signal_id"], "WIN") is True
    assert logger.update_outcome("missing", "WIN") is False


def test_jsonl_signal_logger_dedupes_across_instances_and_seals_torn_tail(tmp_path):
    from paper_engine.signal_logger import SignalLogger

    path = tmp_path / "signals.jsonl"
    first, second = SignalLogger(str(path)), SignalLogger(str(path))
    assert first.log_signal({"signal_id": "s1", "price": 1.0}) is True
    assert second.log_signal({"signal_id": "s1", "price": 1.0}) is False
    with open(path, "a", encoding="utf-8") as handle:
        handle.write('{"signal_id": "torn"')
    assert second.log_signal({"signal_id": "s2", "price": float("inf")}) is True
    lines = path.read_text(encoding="utf-8").split("\n")
    assert lines[1] == '{"signal_id": "torn"'
    assert json.loads(lines[2])["signal_id"] == "s2" and json.loads(lines[2])["price"] is None
    restarted = SignalLogger(str(path))
    assert restarted.has_signal_id("s1") and restarted.has_signal_id("s2")
    assert not restarted.has_signal_id("torn")


def test_kill_switch_never_invents_an_exit_price(tmp_path, monkeypatch):
    from paper_engine import kill_switch
    from paper_engine.portfolio import PaperPortfolio

    monkeypatch.setattr(kill_switch, "KILL_SWITCH_LOCK_FILE", str(tmp_path / "kill.lock"))
    portfolio = PaperPortfolio(filename=str(tmp_path / "pf.json"), ledger_file=str(tmp_path / "ledger.jsonl"))
    portfolio.add_position("p1", "BTCUSDT", "LONG", 100.0, 1.0)
    portfolio.add_position("p2", "ETHUSDT", "LONG", 50.0, 1.0)
    summary = kill_switch.trigger_kill_switch("test", portfolio=portfolio,
                                              current_market_prices={"BTCUSDT": 99.0, "ETHUSDT": float("nan")})
    assert summary["lock_written"] is True and os.path.exists(tmp_path / "kill.lock")
    assert summary["positions_closed"] == 1 and summary["positions_skipped"] == 1
    assert "NO_PRICE_FOR_ETHUSDT" in summary["errors"]
    assert portfolio.positions["p2"]["status"] == "OPEN"


def test_kill_switch_reports_an_unwritable_lock(tmp_path, monkeypatch):
    from paper_engine import kill_switch

    monkeypatch.setattr(kill_switch, "KILL_SWITCH_LOCK_FILE", str(tmp_path / "missing-dir" / "x" / "kill.lock"))

    def boom(*args, **kwargs):
        raise OSError("read-only")

    monkeypatch.setattr(kill_switch, "atomic_write_json", boom)
    summary = kill_switch.trigger_kill_switch("test")
    assert summary["lock_written"] is False
    assert "LOCK_FILE_NOT_WRITTEN" in summary["errors"]


def test_experiment_registry_corruption_stops_registration(tmp_path):
    from paper_engine.experiment_config import FrozenExperimentConfig, register_experiment

    registry = tmp_path / "registry.json"
    registry.write_text('{"experiments": [{"experiment_id": "e0"}', encoding="utf-8")
    with pytest.raises(StateFileError):
        register_experiment(FrozenExperimentConfig(), str(registry))
    assert registry.read_text(encoding="utf-8") == '{"experiments": [{"experiment_id": "e0"}'


def test_experiment_registry_appends_under_lock(tmp_path):
    from paper_engine.experiment_config import FrozenExperimentConfig, register_experiment

    registry = str(tmp_path / "registry.json")
    configs = [FrozenExperimentConfig() for _ in range(3)]
    for cfg in configs:
        register_experiment(cfg, registry)
    register_experiment(configs[0], registry)  # duplicate ignored
    stored = json.loads(open(registry, encoding="utf-8").read())
    assert [e["experiment_id"] for e in stored["experiments"]] == [c.experiment_id for c in configs]


def test_frozen_config_load_rejects_drift_and_corruption(tmp_path):
    from paper_engine.experiment_config import FrozenExperimentConfig

    cfg = FrozenExperimentConfig()
    cfg.save(str(tmp_path))
    assert FrozenExperimentConfig.load(cfg.experiment_id, str(tmp_path)).experiment_id == cfg.experiment_id
    path = tmp_path / f"{cfg.experiment_id}.json"
    data = json.loads(path.read_text())
    data["sneaky_override"] = 1
    path.write_text(json.dumps(data))
    with pytest.raises(StateFileError, match="sneaky_override"):
        FrozenExperimentConfig.load(cfg.experiment_id, str(tmp_path))
    path.write_text("{")
    with pytest.raises(StateFileError):
        FrozenExperimentConfig.load(cfg.experiment_id, str(tmp_path))
    with pytest.raises(FileNotFoundError):
        FrozenExperimentConfig.load("nope", str(tmp_path))


def test_ab_config_load_rejects_unknown_fields(tmp_path):
    from config_ab import ABExperimentConfig

    path = ABExperimentConfig().save(str(tmp_path))
    data = json.loads(open(path).read())
    data["extra"] = True
    with open(path, "w") as handle:
        json.dump(data, handle)
    with pytest.raises(StateFileError, match="extra"):
        ABExperimentConfig.load(path)


# ── quantdinger adapters ────────────────────────────────────────────────────

def test_corrupt_intent_store_blocks_instead_of_resetting(tmp_path):
    from stratex_quantdinger.idempotency import IdempotencyGuard

    path = tmp_path / "intents.json"
    path.write_text('{"intent_1": {"status": "FILLED"', encoding="utf-8")
    guard = IdempotencyGuard(path=str(path))
    assert guard.seen("brand-new-intent") is True
    assert guard.record("brand-new-intent") is False
    assert path.read_text(encoding="utf-8") == '{"intent_1": {"status": "FILLED"'
    assert guard.list_intents() == []


def test_intent_ledger_scan_survives_a_torn_line(tmp_path):
    from stratex_quantdinger.idempotency import IdempotencyGuard

    ledger = tmp_path / "ledger.jsonl"
    ledger.write_text('{"signal_id": "a"}\n{"signal_id": "tor\n{"signal_id": "later"}\n', encoding="utf-8")
    guard = IdempotencyGuard(path=str(tmp_path / "intents.json"), ledger_file=str(ledger))
    assert guard.seen("later") is True
    assert guard.seen("unknown") is False


def test_runtime_lease_file_corruption_is_quarantined(tmp_path):
    from stratex_quantdinger.models import RuntimeHeartbeat
    from stratex_quantdinger.runtime import RuntimeSupervisor

    path = tmp_path / "leases.json"
    path.write_text("[[[", encoding="utf-8")
    supervisor = RuntimeSupervisor(leases_path=str(path))
    supervisor.record_heartbeat(RuntimeHeartbeat("rt1", "s1", "RUNNING", "2026-10-09T00:00:00+00:00"))
    assert set(json.loads(path.read_text())) == {"rt1"}
    assert len(_corrupt_siblings(path)) == 1


# ── generic helpers and other writers ───────────────────────────────────────

def test_publish_new_file_never_overwrites(tmp_path):
    target = tmp_path / "report.json"
    publish_new_file(target, "first")
    with pytest.raises(FileExistsError):
        publish_new_file(target, "second")
    assert target.read_text() == "first"
    assert os.listdir(tmp_path) == ["report.json"]


def test_load_json_document_and_dataclass_helpers(tmp_path):
    import dataclasses

    @dataclasses.dataclass
    class Box:
        width: int
        label: str = "x"

    path = tmp_path / "doc.json"
    with pytest.raises(FileNotFoundError):
        load_json_document(path)
    path.write_text('{"width": NaN}')
    with pytest.raises(StateFileError, match="non-finite"):
        load_json_document(path)
    path.write_text("[1]")
    with pytest.raises(StateFileError, match="expected dict"):
        load_json_document(path)
    assert dataclass_from_mapping(Box, {"width": 3}) == Box(3)
    with pytest.raises(StateFileError, match="unknown"):
        dataclass_from_mapping(Box, {"width": 3, "depth": 1})
    with pytest.raises(StateFileError, match="invalid Box"):
        dataclass_from_mapping(Box, {"label": "no width"})
    with pytest.raises(StateFileError, match="expected a JSON object"):
        dataclass_from_mapping(Box, [3])


def test_self_healing_restores_newest_valid_backup_and_keeps_evidence(tmp_path):
    from autonomy.self_healing import SelfHealingEngine

    backups = tmp_path / "backups"
    engine = SelfHealingEngine(backup_dir=str(backups))
    target = tmp_path / "state.json"
    target.write_text("{corrupt", encoding="utf-8")
    (backups / "state.json.100.bak").write_text('{"v": 100}', encoding="utf-8")
    (backups / "state.json.200.bak").write_text('{"v": 200}', encoding="utf-8")
    (backups / "state.json.300.bak").write_text("{torn", encoding="utf-8")  # newest but unusable
    assert engine.validate_and_repair_state_file(str(target)) is True
    assert json.loads(target.read_text()) == {"v": 200}
    preserved = _corrupt_siblings(target)
    assert len(preserved) == 1 and open(preserved[0]).read() == "{corrupt"


def test_self_healing_refuses_when_no_backup_is_valid(tmp_path):
    from autonomy.self_healing import SelfHealingEngine

    backups = tmp_path / "backups"
    engine = SelfHealingEngine(backup_dir=str(backups))
    (backups / "state.json.1.bak").write_text("nope", encoding="utf-8")
    target = tmp_path / "state.json"
    target.write_text("{corrupt", encoding="utf-8")
    assert engine.restore_latest_good_state(str(target)) is False
    assert target.read_text() == "{corrupt"


def test_production_backup_is_atomic(tmp_path):
    from hardening.production_hardening import ReliabilityHardener

    source = tmp_path / "state.json"
    source.write_text('{"a": 1}', encoding="utf-8")
    hardener = ReliabilityHardener(backup_dir=str(tmp_path / "bk"))
    path = hardener.create_atomic_backup(str(source))
    assert open(path).read() == '{"a": 1}'
    assert [name for name in os.listdir(tmp_path / "bk") if name.endswith(".tmp")] == []


@pytest.mark.parametrize(
    "content,expected",
    [
        (None, "MISSING"),
        ('{"a": 1}\n{"b": 2}\n', "VALID"),
        ('{"a": 1}\n{"b": ', "TORN_TAIL"),
        ('{"a": 1}\n[1]\n', "INVALID"),
        ('{"a": NaN}\n', "INVALID"),
        ('{"a": 1}\n{bad\n{"c": 3}\n', "INVALID"),
    ],
)
def test_forward_runner_jsonl_verification(tmp_path, content, expected):
    import paper_forward_runner

    path = tmp_path / "ledger.jsonl"
    if content is not None:
        path.write_text(content, encoding="utf-8")
    assert paper_forward_runner._verify_jsonl_file(str(path)) == expected


def test_supervisor_treats_future_heartbeat_as_dead(tmp_path, monkeypatch):
    import paper_runner_supervisor as sup

    hb = tmp_path / "hb.json"
    monkeypatch.setattr(sup, "HEARTBEAT_FILE", str(hb))
    monkeypatch.setenv("SUPERVISE_PAPER_RUNNER", "1")
    future = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=2)
    hb.write_text(json.dumps({"status": "RUNNING", "timestamp": future.isoformat()}), encoding="utf-8")
    assert sup.get_status()["paper_runner_status"] == "DEAD"
    fresh = datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)  # naive UTC
    hb.write_text(json.dumps({"status": "RUNNING", "timestamp": fresh.isoformat()}), encoding="utf-8")
    assert sup.get_status()["paper_runner_status"] == "RUNNING"
    hb.write_text("{", encoding="utf-8")
    assert sup.get_status()["paper_runner_status"] == "DEAD"


def test_active_trades_never_persist_non_finite_values(tmp_path, monkeypatch):
    import execution
    from paper_engine.exceptions import StateCorruptionError

    path = tmp_path / "active_trades.json"
    monkeypatch.setattr(execution, "ACTIVE_TRADES_FILE", str(path))
    monkeypatch.chdir(tmp_path)
    path.write_text("[]", encoding="utf-8")
    with pytest.raises(StateCorruptionError, match="Refusing to persist"):
        execution._save_active_trades([{"symbol": "BTCUSDT", "entry_price": float("nan")}])
    assert path.read_text() == "[]"
    path.write_text('[{"entry_price": NaN}]', encoding="utf-8")
    with pytest.raises(StateCorruptionError, match="corrupt"):
        execution._load_active_trades()
    assert path.read_text() == '[{"entry_price": NaN}]'  # left in place for the operator


def test_ledger_records_keep_closed_trades_with_numeric_faults(tmp_path):
    import execution

    ledger = tmp_path / "ledger.jsonl"
    ledger.write_text('{"trade_id": "t0"', encoding="utf-8")  # torn tail from a crash
    execution._append_ledger_record(str(ledger), {"trade_id": "t1", "net_pnl": float("nan"), "fees": [1.0, float("inf")]})
    result = atomic_io.read_jsonl(ledger)
    assert result.truncated_tail is False and result.skipped == 1  # the torn line stays isolated
    record = result.records[0]
    assert record["net_pnl"] is None and record["fees"] == [1.0, None]
    assert record["numeric_fault"] is True and record["numeric_fault_fields"] == ["net_pnl", "fees[1]"]


def test_telemetry_reload_skips_bad_lines_instead_of_stopping(tmp_path):
    from testnet_engine.telemetry_manager import TelemetryManager

    path = os.path.join(str(tmp_path), "testnet_trade_events.jsonl")
    with open(path, "w", encoding="utf-8") as handle:
        handle.write('{"trade_id": "a"}\n{broken\n{"trade_id": "b"}\n')
    reloaded = TelemetryManager(base_dir=str(tmp_path))
    assert {"a", "b"} <= set(reloaded._trade_events)
    reloaded._append_jsonl(path, {"trade_id": "c", "pnl": float("nan")})
    assert json.loads(open(path).read().strip().split("\n")[-1])["pnl"] is None
