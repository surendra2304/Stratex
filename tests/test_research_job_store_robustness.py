"""Regression tests for the durable research-job store and its HTTP endpoint.

Each test pins a defect reproduced against the previous implementation:
lost updates between store instances, a shared ``.tmp`` file, corrupt stores
silently replaced, duplicate job ids executed twice, terminal states
overwritten by late workers, and 500s on wrong-typed request bodies.
"""

from __future__ import annotations

import json
import os
import threading
import time

import pytest

from stratex_quantdinger.jobs import (
    JobAlreadyExistsError,
    JobStore,
    ResearchJobRunner,
)


def _store(tmp_path):
    return JobStore(path=str(tmp_path / "jobs.json"), audit_log_path=str(tmp_path / "audit.jsonl"))


def _wait_for(predicate, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return False


def test_concurrent_creates_from_separate_instances_lose_nothing(tmp_path):
    """Every request used to build its own JobStore with its own lock, so
    concurrent creates overwrote each other's read-modify-write."""
    errors: list[BaseException] = []

    def worker(n: int) -> None:
        try:
            store = _store(tmp_path)  # a fresh instance per "request"
            for i in range(10):
                store.create(f"job_{n}_{i}", "BACKTEST", metadata={"n": n, "i": i})
        except BaseException as exc:  # pragma: no cover - surfaced below
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(n,)) for n in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors, errors
    data = json.loads((tmp_path / "jobs.json").read_text())
    assert len(data) == 80
    leftovers = [p for p in os.listdir(tmp_path) if p.endswith(".tmp")]
    assert leftovers == []


def test_concurrent_updates_to_different_jobs_are_all_kept(tmp_path):
    seed = _store(tmp_path)
    for i in range(6):
        seed.create(f"job_{i}", "BACKTEST")

    def worker(i: int) -> None:
        store = _store(tmp_path)
        for step in range(1, 11):
            store.update(f"job_{i}", status="RUNNING", progress=step / 10)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    final = _store(tmp_path)
    for i in range(6):
        assert final.get(f"job_{i}").progress == 1.0


def test_corrupt_store_is_quarantined_not_overwritten(tmp_path):
    path = tmp_path / "jobs.json"
    path.write_text('{"job_a": {"job_id": "job_a", "job_type": "BACKTEST"')  # truncated
    store = _store(tmp_path)
    store.create("job_new", "BACKTEST")

    quarantined = [p for p in os.listdir(tmp_path) if p.startswith("jobs.json.corrupt-")]
    assert len(quarantined) == 1, os.listdir(tmp_path)
    assert (tmp_path / quarantined[0]).read_text().startswith('{"job_a"')
    assert list(json.loads(path.read_text())) == ["job_new"]


@pytest.mark.parametrize("payload", ["[1, 2]", '"text"', "", "{\"a\": NaN}"])
def test_wrong_top_level_or_invalid_json_is_treated_as_corrupt(tmp_path, payload):
    (tmp_path / "jobs.json").write_text(payload)
    store = _store(tmp_path)
    # Readers report but never move the file (they hold no lock) ...
    assert store.list_jobs() == []
    assert not any(p.startswith("jobs.json.corrupt-") for p in os.listdir(tmp_path))
    # ... the next writer quarantines it under the lock before saving.
    store.create("job_after", "BACKTEST")
    assert any(p.startswith("jobs.json.corrupt-") for p in os.listdir(tmp_path))
    assert [j.job_id for j in store.list_jobs()] == ["job_after"]


def test_unreadable_records_are_skipped_not_500(tmp_path):
    (tmp_path / "jobs.json").write_text(json.dumps({
        "good": {"job_id": "good", "job_type": "BACKTEST", "created_at": "2026-01-01", "extra_field": 1},
        "bad_type": ["not", "a", "record"],
        "missing_id": {"job_type": "BACKTEST"},
    }))
    store = _store(tmp_path)
    jobs = store.list_jobs()
    assert [j.job_id for j in jobs] == ["good"]
    with pytest.raises(KeyError):
        store.get("bad_type")


def test_strict_create_rejects_duplicates_and_runner_never_reexecutes(tmp_path):
    store = _store(tmp_path)
    runner = ResearchJobRunner(store=store)
    calls: list[str] = []

    def work(s, job_id):
        calls.append(job_id)
        s.update(job_id, status="COMPLETED", progress=1.0, result={"value": len(calls)})

    runner.submit_and_execute_async("job_once", "BACKTEST", work)
    assert _wait_for(lambda: store.get("job_once").status == "COMPLETED")
    with pytest.raises(JobAlreadyExistsError):
        runner.submit_and_execute_async("job_once", "BACKTEST", work)
    time.sleep(0.1)
    assert calls == ["job_once"]
    assert store.get("job_once").result == {"value": 1}
    # legacy non-strict create still returns the existing job unchanged
    assert store.create("job_once", "BACKTEST").status == "COMPLETED"


def test_cancelled_job_is_not_resurrected_by_a_late_worker(tmp_path):
    store = _store(tmp_path)
    runner = ResearchJobRunner(store=store)
    release = threading.Event()

    def slow(s, job_id):
        release.wait(5)
        s.update(job_id, status="COMPLETED", progress=1.0, result={"pf": 9.9})

    runner.submit_and_execute_async("job_slow", "BACKTEST", slow)
    assert _wait_for(lambda: store.get("job_slow").status == "RUNNING")
    store.cancel("job_slow")
    release.set()
    time.sleep(0.2)
    job = store.get("job_slow")
    assert job.status == "CANCELLED"
    assert job.result is None
    audit = (tmp_path / "audit.jsonl").read_text()
    assert "UPDATE_REJECTED" in audit


def test_update_rejects_unknown_fields_and_statuses(tmp_path):
    store = _store(tmp_path)
    store.create("job_x", "BACKTEST")
    with pytest.raises(ValueError):
        store.update("job_x", created_at="hijack")
    with pytest.raises(ValueError):
        store.update("job_x", status="DONE")
    with pytest.raises(KeyError):
        store.update("missing", status="RUNNING")


@pytest.mark.parametrize("bad", [
    {"job_id": ""},
    {"job_type": ""},
    {"metadata": ["not", "a", "dict"]},
])
def test_create_validates_arguments(tmp_path, bad):
    store = _store(tmp_path)
    args = {"job_id": "job_v", "job_type": "BACKTEST", "metadata": None}
    args.update(bad)
    with pytest.raises(ValueError):
        store.create(**args)


# ── HTTP endpoint ─────────────────────────────────────────────────────────────

@pytest.fixture
def jobs_client(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    from dashboard import app

    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c


@pytest.mark.parametrize("body", [
    {"job_type": 123},
    {"job_type": "OPTIMIZATION"},
    {"strategy_id": ["adx_ema"]},
    {"strategy_id": "../../etc"},
    {"strategy_id": "os.path"},
    {"job_id": {"nested": 1}},
    {"job_id": "../escape"},
    {"metadata": "nope"},
    {"metadata": {"candles": "500"}},
    {"metadata": {"candles": True}},
    {"metadata": {"candles": 10}},
    {"metadata": {"candles": 10_000_000}},
    {"metadata": {"symbol": 42}},
    {"metadata": {"symbol": "BTC/USDT; rm"}},
    {"metadata": {"timeframe": "7x"}},
])
def test_wrong_typed_research_job_bodies_are_400(jobs_client, control_auth, body):
    res = jobs_client.post("/api/research-jobs", json=body, headers=control_auth)
    assert res.status_code == 400, (body, res.get_data(as_text=True))
    assert res.get_json()["error"] == "INVALID_REQUEST"


def test_duplicate_job_id_is_409_and_default_ids_are_unique(jobs_client, control_auth, tmp_path):
    body = {"strategy_id": "definitely_not_a_real_strategy", "job_id": "dup_job",
            "metadata": {"symbol": "BTCUSDT", "timeframe": "1h", "candles": 50}}
    first = jobs_client.post("/api/research-jobs", json=body, headers=control_auth)
    assert first.status_code == 202
    second = jobs_client.post("/api/research-jobs", json=body, headers=control_auth)
    assert second.status_code == 409
    assert second.get_json()["error"] == "JOB_ALREADY_EXISTS"

    anon = {"strategy_id": "definitely_not_a_real_strategy",
            "metadata": {"symbol": "BTCUSDT", "timeframe": "1h", "candles": 50}}
    ids = {jobs_client.post("/api/research-jobs", json=anon, headers=control_auth).get_json()["job"]["job_id"]
           for _ in range(5)}
    assert len(ids) == 5

    store = JobStore()
    assert _wait_for(lambda: all(store.get(j).status == "FAILED" for j in ids | {"dup_job"}))


def test_research_job_get_tolerates_corrupt_store(jobs_client, tmp_path):
    (tmp_path / "experiment_jobs.json").write_text("{corrupt")
    res = jobs_client.get("/api/research-jobs")
    assert res.status_code == 200
    assert res.get_json()["count"] == 0
