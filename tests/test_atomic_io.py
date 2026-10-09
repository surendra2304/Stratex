"""Tests for atomic_io: atomic replacement, path locks, corrupt-state
quarantine and tolerant JSONL handling."""

from __future__ import annotations

import json
import multiprocessing
import os
import threading

import pytest

import atomic_io
from atomic_io import (
    append_jsonl,
    atomic_write_bytes,
    atomic_write_json,
    atomic_write_text,
    encode_jsonl_record,
    finite_or_none,
    load_json_state,
    locked_path,
    parse_json_strict,
    quarantine_file,
    read_jsonl,
)


def test_atomic_write_text_and_bytes_replace_whole_file(tmp_path):
    target = tmp_path / "state.json"
    atomic_write_text(target, "first")
    atomic_write_text(target, "second")
    assert target.read_text() == "second"
    atomic_write_bytes(target, b"\x00\x01")
    assert target.read_bytes() == b"\x00\x01"
    assert [p for p in os.listdir(tmp_path) if p.endswith(".tmp")] == []


def test_atomic_write_json_never_touches_disk_on_serialization_error(tmp_path):
    target = tmp_path / "state.json"
    atomic_write_json(target, {"ok": 1})
    with pytest.raises(TypeError):
        atomic_write_json(target, {"bad": object()})
    assert json.loads(target.read_text()) == {"ok": 1}
    assert [p for p in os.listdir(tmp_path) if p.endswith(".tmp")] == []


def test_atomic_write_cleans_up_temp_file_when_replace_fails(tmp_path, monkeypatch):
    target = tmp_path / "state.json"

    def boom(src, dst):
        raise OSError("disk full")

    monkeypatch.setattr(atomic_io.os, "replace", boom)
    with pytest.raises(OSError):
        atomic_write_text(target, "data")
    assert os.listdir(tmp_path) == []


def test_concurrent_atomic_writers_never_collide(tmp_path):
    target = tmp_path / "state.json"
    errors: list[BaseException] = []

    def writer(n: int) -> None:
        try:
            for i in range(50):
                atomic_write_json(target, {"writer": n, "i": i}, fsync=False)
        except BaseException as exc:  # pragma: no cover - reported below
            errors.append(exc)

    threads = [threading.Thread(target=writer, args=(n,)) for n in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors
    assert set(json.loads(target.read_text())) == {"writer", "i"}


def _increment_in_subprocess(path: str, rounds: int) -> None:
    for _ in range(rounds):
        with locked_path(path):
            current = json.loads(open(path).read()) if os.path.exists(path) else {"n": 0}
            current["n"] += 1
            atomic_write_json(path, current, fsync=False)


def test_locked_path_serializes_read_modify_write_across_processes(tmp_path):
    path = str(tmp_path / "counter.json")
    ctx = multiprocessing.get_context("fork")
    procs = [ctx.Process(target=_increment_in_subprocess, args=(path, 40)) for _ in range(4)]
    for p in procs:
        p.start()
    _increment_in_subprocess(path, 40)
    for p in procs:
        p.join(30)
        assert p.exitcode == 0
    assert json.loads(open(path).read()) == {"n": 200}


def test_locked_path_is_reentrant_and_serializes_threads(tmp_path):
    path = str(tmp_path / "counter.json")
    with locked_path(path):
        with locked_path(path):  # must not deadlock
            atomic_write_json(path, {"n": 0}, fsync=False)

    def bump() -> None:
        for _ in range(100):
            with locked_path(path):
                data = json.loads(open(path).read())
                data["n"] += 1
                atomic_write_json(path, data, fsync=False)

    threads = [threading.Thread(target=bump) for _ in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert json.loads(open(path).read()) == {"n": 600}


def test_quarantine_file_preserves_evidence(tmp_path):
    target = tmp_path / "ledger.json"
    target.write_text("{broken")
    moved = quarantine_file(target, "test")
    assert moved is not None and os.path.exists(moved)
    assert open(moved).read() == "{broken"
    assert not target.exists()
    assert quarantine_file(target, "gone") is None


class TestLoadJsonState:
    def test_missing(self, tmp_path):
        result = load_json_state(tmp_path / "nope.json")
        assert result.status == "missing" and result.data == {}

    def test_ok(self, tmp_path):
        p = tmp_path / "s.json"
        p.write_text('{"a": 1}')
        result = load_json_state(p)
        assert result.ok and result.data == {"a": 1}

    @pytest.mark.parametrize("content, reason", [
        ("", "empty"), ("   ", "empty"), ("{", "invalid JSON"), ("[1]", "expected dict"),
        ('{"x": NaN}', "invalid JSON"), ('{"x": Infinity}', "invalid JSON"),
    ])
    def test_corrupt_is_quarantined(self, tmp_path, content, reason):
        p = tmp_path / "s.json"
        p.write_text(content)
        result = load_json_state(p)
        assert result.status == "corrupt"
        assert reason in result.error
        assert result.data == {}
        assert result.quarantined_to and os.path.exists(result.quarantined_to)
        assert not p.exists()

    def test_quarantine_can_be_disabled(self, tmp_path):
        p = tmp_path / "s.json"
        p.write_text("{")
        result = load_json_state(p, quarantine=False)
        assert result.status == "corrupt" and p.exists() and result.quarantined_to is None

    def test_list_state_and_custom_default(self, tmp_path):
        p = tmp_path / "s.json"
        p.write_text("[1, 2]")
        assert load_json_state(p, expected_type=list, default_factory=list).data == [1, 2]
        p.write_text("{}")
        result = load_json_state(p, expected_type=list, default_factory=list)
        assert result.status == "corrupt" and result.data == []

    def test_oversized_file(self, tmp_path):
        p = tmp_path / "s.json"
        p.write_text(json.dumps({"x": "y" * 100}))
        result = load_json_state(p, max_bytes=10)
        assert result.status == "corrupt" and "safety limit" in result.error

    def test_non_finite_allowed_when_requested(self, tmp_path):
        p = tmp_path / "s.json"
        p.write_text('{"x": NaN}')
        result = load_json_state(p, allow_non_finite=True)
        assert result.ok


class TestJsonl:
    def test_append_and_read_round_trip(self, tmp_path):
        p = tmp_path / "log.jsonl"
        for i in range(5):
            append_jsonl(p, {"i": i})
        result = read_jsonl(p)
        assert [r["i"] for r in result.records] == list(range(5))
        assert result.clean

    def test_torn_tail_is_skipped_and_next_append_starts_a_new_line(self, tmp_path):
        p = tmp_path / "log.jsonl"
        append_jsonl(p, {"i": 0})
        with open(p, "a") as fh:
            fh.write('{"i": 1, "trunc')  # crash mid-append
        before = read_jsonl(p)
        assert [r["i"] for r in before.records] == [0]
        assert before.skipped == 1 and before.truncated_tail
        append_jsonl(p, {"i": 2})
        after = read_jsonl(p)
        assert [r["i"] for r in after.records] == [0, 2]
        assert after.skipped == 1 and not after.truncated_tail

    def test_corrupt_middle_lines_and_wrong_types_are_counted(self, tmp_path):
        p = tmp_path / "log.jsonl"
        p.write_text('{"a": 1}\nnot json\n[1, 2]\n\n{"b": NaN}\n{"c": 3}\n')
        result = read_jsonl(p)
        assert result.records == [{"a": 1}, {"c": 3}]
        assert result.skipped == 3
        assert len(result.errors) == 3

    def test_missing_file_is_empty_and_clean(self, tmp_path):
        result = read_jsonl(tmp_path / "none.jsonl")
        assert result.records == [] and result.clean

    def test_concurrent_appenders_never_interleave(self, tmp_path):
        p = tmp_path / "log.jsonl"

        def worker(n: int) -> None:
            for i in range(100):
                append_jsonl(p, {"n": n, "i": i, "pad": "x" * 500})

        threads = [threading.Thread(target=worker, args=(n,)) for n in range(6)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        result = read_jsonl(p)
        assert result.clean and len(result.records) == 600

    def test_encode_rejects_non_finite(self):
        with pytest.raises(ValueError):
            encode_jsonl_record({"x": float("nan")})
        line = encode_jsonl_record({"t": __import__("datetime").datetime(2026, 1, 1)})
        assert line.endswith("\n") and "2026-01-01" in line


def test_parse_json_strict_and_finite_or_none():
    assert parse_json_strict('{"a": 1.5}') == {"a": 1.5}
    with pytest.raises(ValueError):
        parse_json_strict("[NaN]")
    assert finite_or_none("2.5") == 2.5
    for bad in (None, True, "nan", "inf", float("inf"), "x", [1]):
        assert finite_or_none(bad) is None
