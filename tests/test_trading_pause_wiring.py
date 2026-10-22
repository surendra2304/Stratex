"""Regression tests: the control-plane pause must actually stop the engine.

Historical defect this pins shut: POST /api/v1/control/pause only flipped an
in-process boolean inside the dashboard process, while the trading engine runs
in a SEPARATE process — so "paused" was decorative and the scanner kept opening
positions. The pause is now a durable file flag (trading_pause.py) that
TestnetService consults before every order submission.
"""
import json

import pytest

import trading_pause


@pytest.fixture()
def pause_file(tmp_path, monkeypatch):
    path = tmp_path / "trading_pause_state.json"
    monkeypatch.setenv("TRADING_PAUSE_STATE_FILE", str(path))
    return path


class TestPauseFlagModule:
    def test_default_is_not_paused(self, pause_file):
        assert trading_pause.is_trading_paused() is False
        assert trading_pause.get_pause_state()["reason"] == "NO_PAUSE_FILE"

    def test_set_and_clear_roundtrip(self, pause_file):
        trading_pause.set_trading_paused(True, actor="unit-test")
        assert trading_pause.is_trading_paused() is True
        state = trading_pause.get_pause_state()
        assert state["paused"] is True
        assert state["actor"] == "unit-test"
        # The flag must be durable JSON (cross-process contract).
        on_disk = json.loads(pause_file.read_text(encoding="utf-8"))
        assert on_disk["paused"] is True

        trading_pause.set_trading_paused(False, actor="unit-test")
        assert trading_pause.is_trading_paused() is False

    def test_corrupt_flag_file_fails_closed_and_loudly(self, pause_file):
        # The flag file only exists because a pause state was written; if it is
        # unreadable we cannot prove nobody paused trading -> block new entries.
        for corrupt in ("{not json", "", "[1, 2]", "null"):
            pause_file.write_text(corrupt, encoding="utf-8")
            state = trading_pause.get_pause_state()
            assert state["paused"] is True, corrupt
            assert state["reason"].startswith("CORRUPT_FILE:")
            assert trading_pause.is_trading_paused() is True
        # The resume API path (set_trading_paused(False)) repairs it.
        trading_pause.set_trading_paused(False, actor="unit-test")
        assert trading_pause.is_trading_paused() is False

    def test_concurrent_pause_resume_writes_never_collide(self, pause_file):
        import threading

        errors = []

        def flip(i):
            try:
                trading_pause.set_trading_paused(i % 2 == 0, actor=f"t{i}")
            except Exception as exc:  # pragma: no cover - the regression
                errors.append(exc)

        threads = [threading.Thread(target=flip, args=(i,)) for i in range(64)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert errors == []
        assert trading_pause.get_pause_state()["reason"] == "OK"
        leftovers = [p.name for p in pause_file.parent.iterdir() if p.name.endswith(".tmp")]
        assert leftovers == []


class TestControlApiPersistsPause:
    def _client(self, monkeypatch, pause_file):
        from dashboard import app
        monkeypatch.setenv("TRADING_BOT_API_KEY_CONTROL", "test-control-key-for-pause-wiring")
        return app.test_client()

    def test_pause_and_resume_endpoints_write_durable_flag(self, monkeypatch, pause_file):
        client = self._client(monkeypatch, pause_file)

        res = client.post("/api/v1/control/pause", headers={"X-API-Key": "test-control-key-for-pause-wiring"})
        assert res.status_code == 200
        assert trading_pause.is_trading_paused() is True

        res = client.post("/api/v1/control/resume", headers={"X-API-Key": "test-control-key-for-pause-wiring"})
        assert res.status_code == 200
        assert trading_pause.is_trading_paused() is False

    def test_pause_requires_control_key(self, monkeypatch, pause_file):
        # No key configured anywhere -> control endpoints fail closed.
        for k in ("TRADING_BOT_API_KEY_CONTROL", "TRADING_BOT_API_KEY_READ",
                  "BOT_API_KEY", "API_KEY_CONTROL", "API_KEY_READONLY", "API_KEY_FRIDAY"):
            monkeypatch.delenv(k, raising=False)
        from dashboard import app
        res = app.test_client().post("/api/v1/control/pause")
        assert res.status_code in (401, 503)
        assert trading_pause.is_trading_paused() is False


class TestEngineHonoursPause:
    def _bare_service(self):
        """A TestnetService shell with only the attributes the pause guard
        touches — no network, no exchange client."""
        import testnet_engine.service as svc
        service = object.__new__(svc.TestnetService)
        service.stats = {}
        service.rejections = []

        def _record(signal_id, symbol, side, metrics, decision, reason, *args, **kwargs):
            service.rejections.append((signal_id, symbol, side, decision, reason))

        service.log_opportunity = _record
        return service

    def test_engine_rejects_candidates_while_paused(self, pause_file):
        service = self._bare_service()
        candidates = [
            {"signal_id": "sig-1", "symbol": "BTCUSDT", "side": "BUY"},
            {"signal_id": "sig-2", "symbol": "ETHUSDT", "side": "SELL"},
        ]

        trading_pause.set_trading_paused(True, actor="engine-test")
        assert service._reject_paused_candidates(candidates) is True
        assert service.stats["TRADING_PAUSED_SKIPPED"] == 2
        assert all(r[3] == "REJECTED" and r[4] == "TRADING_PAUSED" for r in service.rejections)

        trading_pause.set_trading_paused(False, actor="engine-test")
        assert service._reject_paused_candidates(candidates) is False
        assert service.stats["TRADING_PAUSED_SKIPPED"] == 2  # unchanged

    def test_execution_loop_calls_the_pause_guard(self):
        """The engine's main loop must actually consult the durable pause guard."""
        import inspect
        import testnet_engine.service as svc
        source = inspect.getsource(svc.TestnetService.execution_loop)
        assert "_reject_paused_candidates" in source, (
            "execution_loop no longer consults the durable pause flag — the "
            "control-plane pause would silently stop being enforced."
        )
        assert "is_trading_paused" in source, (
            "execution_loop lost its submission-boundary pause re-check."
        )
