"""Regression tests for the 'healthy but inert' engine lie.

Measured against the live Render service on 2026-10-03, `/api/status` served:

    "engine_status": "ONLINE", "healthy": true, "strategies": [],
    "last_candle_close": 38 minutes stale, components.strategy == "OK"

That is a process that is alive and economically inert: the governance gate
correctly refused to load unvalidated strategies, so the engine was evaluating
nothing and could never place a trade — while every health surface said OK.

These tests pin the distinction the live service was missing.
"""

import datetime
import json
import os

import pytest

import config
import paper_runner_supervisor
from dashboard import app, get_engine_health_data


@pytest.fixture
def client():
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c


def _hb(tmp_path, monkeypatch, **overrides):
    """Write a heartbeat file and point the dashboard at it."""
    hb = {
        "status": "RUNNING",
        "worker_alive": True,
        "binance_connected": True,
        "websocket_connected": True,
        "pid": os.getpid(),
        "timestamp": datetime.datetime.utcnow().isoformat() + "Z",
        "strategies": ["SUPERTREND"],
        "timeframes": ["1h"],
        "last_strategy_evaluation": datetime.datetime.utcnow().isoformat() + "Z",
    }
    hb.update(overrides)
    f = tmp_path / "testnet_heartbeat.json"
    f.write_text(json.dumps(hb))
    monkeypatch.setenv("TESTNET_HEARTBEAT_FILE", str(f))
    # The paper runner is a separate process and would mask assertions here.
    monkeypatch.setattr(
        paper_runner_supervisor,
        "get_status",
        lambda: {"paper_runner_status": "STOPPED", "alive": False},
    )
    return f


class TestHealthyIsNotCapable:
    def test_live_process_with_zero_strategies_is_alive_but_not_capable(self, tmp_path, monkeypatch):
        """Reproduces the live 2026-10-03 observation exactly."""
        _hb(tmp_path, monkeypatch, strategies=[], timeframes=[], last_strategy_evaluation=None)

        data = get_engine_health_data()

        # Liveness is preserved and must not be over-corrected into OFFLINE.
        assert data["healthy"] is True
        assert data["engine_status"] == "ONLINE"
        # Capability is the missing signal.
        assert data["trading_capable"] is False
        assert any("NO_EXECUTABLE_STRATEGY" in r for r in data["capability_reasons"])

    def test_stale_strategy_evaluation_is_reported_not_hidden(self, tmp_path, monkeypatch):
        """38-minute-stale evaluation must be named, with the measured age."""
        stale = datetime.datetime.utcnow() - datetime.timedelta(minutes=38)
        _hb(tmp_path, monkeypatch, last_strategy_evaluation=stale.isoformat() + "Z")

        data = get_engine_health_data()

        assert data["healthy"] is True
        assert data["trading_capable"] is False
        reason = " ".join(data["capability_reasons"])
        assert "STALE_STRATEGY_EVALUATION" in reason
        # The age must be reported so the claim is falsifiable, not just asserted.
        assert "2280s" in reason or "22" in reason

    def test_capable_engine_is_not_penalised(self, tmp_path, monkeypatch):
        """A genuinely working engine must still report capable — no false alarms."""
        _hb(tmp_path, monkeypatch)

        data = get_engine_health_data()

        assert data["healthy"] is True
        assert data["trading_capable"] is True
        assert data["capability_reasons"] == []

    def test_unreadable_evaluation_timestamp_is_not_silently_treated_as_fresh(self, tmp_path, monkeypatch):
        _hb(tmp_path, monkeypatch, last_strategy_evaluation="not-a-timestamp")

        data = get_engine_health_data()

        assert data["trading_capable"] is False
        assert any("UNREADABLE_EVALUATION_TIMESTAMP" in r for r in data["capability_reasons"])


class TestReadinessGateRefusesInertEngine:
    def test_ready_must_fail_when_engine_cannot_act(self, tmp_path, monkeypatch, client):
        """An inert engine must not pass /ready, or Render routes real work to it."""
        _hb(tmp_path, monkeypatch, strategies=[], last_strategy_evaluation=None)

        readiness = client.get("/ready")

        assert readiness.status_code == 503
        body = readiness.get_json()
        assert body["status"] == "not_ready"
        assert body["engine_healthy"] is True  # liveness honestly still true
        assert body["engine_trading_capable"] is False
        assert body["capability_reasons"]

    def test_ready_passes_for_a_capable_engine(self, tmp_path, monkeypatch, client):
        _hb(tmp_path, monkeypatch)

        readiness = client.get("/ready")

        assert readiness.status_code == 200
        assert readiness.get_json()["status"] == "ready"

    def test_liveness_still_passes_for_an_inert_engine(self, tmp_path, monkeypatch, client):
        """Render must not kill-loop a process that is up; /health stays 200."""
        _hb(tmp_path, monkeypatch, strategies=[], last_strategy_evaluation=None)

        assert client.get("/health").status_code == 200


class TestStatusEndpointDoesNotClaimStrategyIsOK:
    def _status(self, client, **hb_overrides):
        return client.get("/api/status").get_json()

    def test_strategy_component_reports_idle_not_ok_when_inert(self, tmp_path, monkeypatch, client):
        _hb(tmp_path, monkeypatch, strategies=[], last_strategy_evaluation=None)

        body = self._status(client)

        assert body["components"]["strategy"] == "IDLE"
        assert body["overall_health"] == "DEGRADED"
        assert body["engine_trading_capable"] is False

    def test_strategy_component_reports_ok_when_capable(self, tmp_path, monkeypatch, client):
        _hb(tmp_path, monkeypatch)

        body = self._status(client)

        assert body["components"]["strategy"] == "OK"

    def test_inert_engine_is_not_a_strategic_paper_allocation_win(self, tmp_path, monkeypatch, client):
        """Paper mode must not launder an inert engine into a healthy verdict."""
        monkeypatch.setattr(config, "TRADING_MODE", "PAPER")
        _hb(tmp_path, monkeypatch, strategies=[], last_strategy_evaluation=None)

        body = self._status(client)

        # The paper-runner upgrade path may mark the engine alive, but capability
        # is still computed from what the engine actually loaded.
        assert body["engine_trading_capable"] is False
        assert body["components"]["strategy"] == "IDLE"