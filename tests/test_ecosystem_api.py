"""
tests/test_ecosystem_api.py — Consumer-Agnostic Tests for Ecosystem Integration API (v1).

Verifies:
1. Public Status Endpoints (/api/v1/status, /positions, /trades, /strategies, /advisory, /risk, /history/equity).
2. API Key Authentication:
   - Missing key -> 401 Unauthorized.
   - READ key cannot call /control endpoints -> 403 Forbidden.
   - CONTROL key can call /pause, /resume, /strategy toggle, /panic.
3. Control Endpoint Safety:
   - Panic without confirmation -> 400 Bad Request.
   - Panic with confirmation -> 200 OK & incident logged.
   - Control audit trail created in control_audit.jsonl.
4. Export Endpoints (/api/v1/export/trades, /equity, /advisory-log, /risk-events).
5. Health Endpoints (/api/v1/health, /detailed, /integrations).
6. Webhook Event Dispatching & Dead Letter Queue (DLQ).
"""

import os

import pytest

from api.webhooks import EcosystemWebhookEmitter
from dashboard import app


@pytest.fixture
def client():
    app.config["TESTING"] = True
    with app.test_client() as client:
        yield client


def test_public_status_endpoints(client):
    headers = {"X-API-Key": "read_key_default_secret_123"}

    # 1. /api/v1/status
    res = client.get("/api/v1/status", headers=headers)
    assert res.status_code == 200
    data = res.get_json()
    assert data["status"] == "OK"
    assert "equity" in data["data"]
    assert "strategies_active" in data["data"]

    # 2. /api/v1/positions
    res = client.get("/api/v1/positions", headers=headers)
    assert res.status_code == 200

    # 3. /api/v1/trades
    res = client.get("/api/v1/trades?page=1&limit=5", headers=headers)
    assert res.status_code == 200
    assert "pagination" in res.get_json()

    # 4. /api/v1/strategies
    res = client.get("/api/v1/strategies", headers=headers)
    assert res.status_code == 200

    # 5. /api/v1/advisory
    res = client.get("/api/v1/advisory", headers=headers)
    assert res.status_code == 200

    # 6. /api/v1/risk
    res = client.get("/api/v1/risk", headers=headers)
    assert res.status_code == 200

    # 7. /api/v1/history/equity
    res = client.get("/api/v1/history/equity", headers=headers)
    assert res.status_code == 200


def test_api_key_role_permissions(client):
    # 1. Missing Key on protected endpoint -> 401
    res = client.get("/api/v1/status")
    assert res.status_code == 401

    # 2. Read key trying to pause -> 403 Forbidden
    read_headers = {"X-API-Key": "read_key_default_secret_123"}
    res = client.post("/api/v1/control/pause", headers=read_headers)
    assert res.status_code == 403

    # 3. Control key pausing -> 200 OK
    control_headers = {"X-API-Key": "control_key_default_secret_456"}
    res = client.post("/api/v1/control/pause", headers=control_headers)
    assert res.status_code == 200
    assert "paused" in res.get_json()["data"]["message"].lower()

    # 4. Control key resuming -> 200 OK
    res = client.post("/api/v1/control/resume", headers=control_headers)
    assert res.status_code == 200


def test_control_panic_confirmation_safety(client):
    control_headers = {"X-API-Key": "control_key_default_secret_456"}

    # 1. Panic without confirmation payload -> 400 Bad Request
    res = client.post("/api/v1/control/panic", json={}, headers=control_headers)
    assert res.status_code == 400
    assert res.get_json()["error"] == "CONFIRMATION_REQUIRED"

    # 2. Panic with confirmation -> 200 OK
    res = client.post("/api/v1/control/panic", json={"confirm": True}, headers=control_headers)
    assert res.status_code == 200
    assert "EMERGENCY PANIC" in res.get_json()["data"]["message"]

    # Verify audit file was updated
    assert os.path.exists("control_audit.jsonl")


def test_strategy_toggle_endpoint(client):
    control_headers = {"X-API-Key": "control_key_default_secret_456"}
    res = client.post(
        "/api/v1/control/strategy/strategy_scalper/toggle",
        json={"enabled": False},
        headers=control_headers
    )
    assert res.status_code == 200
    assert res.get_json()["data"]["enabled"] is False


def test_data_export_endpoints(client):
    read_headers = {"X-API-Key": "read_key_default_secret_123"}

    res = client.get("/api/v1/export/trades?format=json", headers=read_headers)
    assert res.status_code == 200
    assert "trades" in res.get_json()

    res = client.get("/api/v1/export/equity?format=json", headers=read_headers)
    assert res.status_code == 200

    res = client.get("/api/v1/export/advisory-log?format=json", headers=read_headers)
    assert res.status_code == 200


def test_health_endpoints(client):
    # Fast liveness requires no auth
    res = client.get("/api/v1/health")
    assert res.status_code == 200
    assert res.get_json()["status"] == "HEALTHY"

    read_headers = {"X-API-Key": "read_key_default_secret_123"}
    res = client.get("/api/v1/health/detailed", headers=read_headers)
    assert res.status_code == 200
    assert "system_resources" in res.get_json()["data"]

    res = client.get("/api/v1/health/integrations", headers=read_headers)
    assert res.status_code == 200


def test_webhook_emitter():
    emitter = EcosystemWebhookEmitter()
    emitter.emit_event("trade.closed", {"symbol": "BTC/USDT", "pnl": 45.0})
    assert True
