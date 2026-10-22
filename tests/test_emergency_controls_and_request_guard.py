"""Regression tests for defects found by the hostile-body live POST census.

* 28 HTTP 500s from JSON-array / malformed bodies (handlers assumed a dict);
* /api/panic failed (500, panic NOT engaged) on an unexpected body and treated
  ``{"release": "false"}`` as a release; FRIDAY panic did the same and its task
  path reported "SUCCESS" without engaging anything;
* panic_state.json schema split: /api/panic wrote ``active`` but the execution
  gate only read ``panic_active`` (and vice versa for the testnet engine), and
  corrupt flags were ignored;
* /api/v1/control/panic and the /api/live/emergency/* endpoints answered
  "positions flattened / kill switch engaged / entries halted" while nothing
  durable happened (throw-away objects, no flags written);
* FRIDAY supervision mutations (panic/release, advisory authorization, task
  dispatch) required no authentication at all;
* concurrent pause/resume writes collided on a shared ``.tmp`` file (500).
"""

import json
import os

import pytest
from flask import Flask

import execution
import trading_pause
from api.request_guard import install_request_guards
from panic_state import (
    engage_order_block,
    is_kill_switch_locked,
    is_panic_active,
    read_panic_state,
    write_panic_state,
)


@pytest.fixture
def client():
    from dashboard import app

    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c


def _panic_path():
    return os.environ["PANIC_STATE_FILE"]


# ── request guard ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize("path", [
    "/api/v1/nautilus/risk/check",
    "/api/v1/nautilus/simulate",
    "/api/v1/backtrader/run",
    "/api/v1/freqtrade/backtest",
    "/api/research-jobs",
    "/api/agent-gateway/jobs",
    "/api/testnet/positions/close",
    "/v1/task/execute",
])
def test_non_object_and_invalid_json_bodies_are_400_not_500(client, control_auth, path):
    for body in ("[1, 2, 3]", '"text"', "17", "{bad json", b"\xff\xfe"):
        res = client.post(path, data=body, headers={**control_auth, "Content-Type": "application/json"})
        assert res.status_code == 400, (path, body, res.status_code)
        assert res.get_json()["error"] in {"INVALID_JSON", "JSON_OBJECT_REQUIRED"}
    # non-JSON content type with a JSON array (force=True handlers) is rejected too
    res = client.post(path, data="[1]", headers={**control_auth, "Content-Type": "text/plain"})
    assert res.status_code == 400


def test_guard_leaves_unknown_routes_and_empty_bodies_alone(client):
    # unknown route: Flask's own 404/405 (the SPA catch-all is GET-only), not a body error
    assert client.post("/api/definitely-not-a-route", data="[1]",
                       headers={"Content-Type": "application/json"}).status_code in (404, 405)
    # empty body still reaches the handler (here: auth refusal, not a body error)
    assert client.post("/api/strategy-registry").status_code in (401, 503)


def test_guard_size_limit_and_json_500_envelope():
    app = Flask("guard-test")
    app.config["MAX_CONTENT_LENGTH"] = 2048
    install_request_guards(app)

    @app.route("/api/boom", methods=["POST"])
    def boom():
        raise RuntimeError("secret internal detail")

    @app.route("/api/echo", methods=["POST"])
    def echo():
        return {"ok": True}

    c = app.test_client()
    big = json.dumps({"x": "a" * 4096})
    res = c.post("/api/echo", data=big, headers={"Content-Type": "application/json"})
    assert res.status_code == 413 and res.get_json()["error"] == "REQUEST_TOO_LARGE"
    res = c.post("/api/boom", json={})
    assert res.status_code == 500
    assert res.get_json()["error"] == "INTERNAL_ERROR"
    assert "secret internal detail" not in res.get_data(as_text=True)


# ── /api/panic ────────────────────────────────────────────────────────────────

def test_api_panic_engages_on_any_body_and_only_releases_on_literal_true(client, control_auth):
    for body in ("[1, 2, 3]", '"stop"', "{bad json", ""):
        os.path.exists(_panic_path()) and os.remove(_panic_path())
        res = client.post("/api/panic", data=body, headers={**control_auth, "Content-Type": "application/json"})
        assert res.status_code == 200, (body, res.get_data(as_text=True))
        assert res.get_json()["panic_active"] is True
        assert is_panic_active()

    for not_a_release in ("false", "no", 1, "true"):
        res = client.post("/api/panic", json={"release": not_a_release}, headers=control_auth)
        assert res.get_json()["panic_active"] is True, not_a_release
        assert is_panic_active()

    res = client.post("/api/panic", json={"release": True}, headers=control_auth)
    assert res.status_code == 200 and res.get_json()["panic_active"] is False
    assert not is_panic_active()


# ── unified panic flag ────────────────────────────────────────────────────────

@pytest.mark.parametrize("content", [
    {"active": True},          # legacy /api/panic schema
    {"panic_active": True},    # legacy FRIDAY schema
])
def test_both_legacy_panic_schemas_block_every_gate(content):
    from testnet_engine.service import TestnetService

    with open(_panic_path(), "w", encoding="utf-8") as fh:
        json.dump(content, fh)
    with pytest.raises(RuntimeError, match="Kill-Switch is active"):
        execution._check_panic_and_kill_switch()
    svc = TestnetService.__new__(TestnetService)
    svc.PANIC_STATE_FILE = _panic_path()
    assert svc.panic_active() is True


@pytest.mark.parametrize("corrupt", ["{truncated", "", "[]", "null", "\x00\x01"])
def test_corrupt_panic_flag_fails_closed(corrupt):
    with open(_panic_path(), "w", encoding="utf-8") as fh:
        fh.write(corrupt)
    state = read_panic_state()
    assert state["active"] is True and state["reason"].startswith("CORRUPT_FILE:")
    with pytest.raises(RuntimeError):
        execution._check_panic_and_kill_switch()


def test_panic_writes_are_atomic_and_unified(tmp_path):
    path = str(tmp_path / "p.json")
    state = write_panic_state(True, actor="t", reason="r", path=path)
    assert state["active"] is True and state["panic_active"] is True
    write_panic_state(False, actor="t", reason="r", path=path)
    with open(path, encoding="utf-8") as fh:
        on_disk = json.load(fh)
    assert on_disk["active"] is False and on_disk["panic_active"] is False
    assert [p.name for p in tmp_path.iterdir()] == ["p.json"]


# ── FRIDAY supervision ────────────────────────────────────────────────────────

def test_friday_mutations_require_authentication(client):
    for path, body in (
        ("/api/v1/friday/panic", {"confirm": True, "release": True}),
        ("/v1/friday/supervision/panic", {"confirm": True}),
        ("/api/v1/friday/advisory/authorize", {"recommendation_id": "x"}),
        ("/v1/friday/task", {"action": "panic", "payload": {"confirm": True}}),
    ):
        assert client.post(path, json=body).status_code in (401, 503), path
    assert not is_panic_active()


def test_friday_panic_requires_literal_true(client, friday_auth):
    res = client.post("/api/v1/friday/panic", json={"confirm": "false", "release": "false"}, headers=friday_auth)
    assert res.status_code == 400 and res.get_json()["error"] == "CONFIRMATION_REQUIRED"
    res = client.post("/api/v1/friday/panic", data="[1]", headers={**friday_auth, "Content-Type": "application/json"})
    assert res.status_code == 400

    res = client.post("/api/v1/friday/panic", json={"confirm": True, "release": "yes"}, headers=friday_auth)
    assert res.status_code == 200 and res.get_json()["panic_active"] is True  # "yes" is not a release
    assert is_panic_active() and is_kill_switch_locked()
    assert "cancelled" not in res.get_json()["message"].lower().replace("not cancelled", "")

    res = client.post("/api/v1/friday/panic", json={"confirm": True, "release": True}, headers=friday_auth)
    assert res.status_code == 200 and res.get_json()["panic_active"] is False
    assert not is_panic_active() and not is_kill_switch_locked()


def test_friday_task_panic_really_engages(client, friday_auth):
    res = client.post("/v1/friday/task", json={"action": "panic", "source_agent": "friday",
                                               "payload": {"confirm": True, "reason": "voice"}},
                      headers=friday_auth)
    assert res.status_code == 200
    assert res.get_json()["status"] == "SUCCESS"
    assert is_panic_active() and is_kill_switch_locked()


# ── control + live emergency endpoints ────────────────────────────────────────

@pytest.fixture
def control_api_auth(monkeypatch):
    """The /api/v1/control blueprint authenticates with TRADING_BOT_API_KEY_CONTROL."""
    key = "pytest-control-api-key-not-a-real-credential"
    monkeypatch.setenv("TRADING_BOT_API_KEY_CONTROL", key)
    return {"X-API-Key": key}


def test_control_panic_blocks_orders_and_reports_honestly(client, control_api_auth):
    assert client.post("/api/v1/control/panic", json={"confirm": True}).status_code == 401
    res = client.post("/api/v1/control/panic", json={"confirm": "true"}, headers=control_api_auth)
    assert res.status_code == 400  # string is not an explicit confirmation
    res = client.post("/api/v1/control/panic", json={"confirm": True}, headers=control_api_auth)
    assert res.status_code == 200
    body = res.get_json()
    payload = body.get("data", body)
    assert payload["steps"]["panic_flag"] == "WRITTEN"
    assert payload["steps"]["trading_pause"] == "WRITTEN"
    assert "NOT flattened" in payload["message"]
    assert is_panic_active() and trading_pause.is_trading_paused()
    with pytest.raises(RuntimeError):
        execution._check_panic_and_kill_switch()


def test_control_panic_reports_failure_when_nothing_persists(client, control_api_auth, monkeypatch):
    import panic_state

    def broken(*_a, **_k):
        raise OSError("disk full")

    monkeypatch.setattr(panic_state, "write_panic_state", broken)
    monkeypatch.setattr(trading_pause, "set_trading_paused", broken)
    res = client.post("/api/v1/control/panic", json={"confirm": True}, headers=control_api_auth)
    assert res.status_code == 500
    assert res.get_json()["error"] == "PANIC_NOT_PERSISTED"


def test_engage_order_block_attempts_every_mechanism(monkeypatch):
    import panic_state

    monkeypatch.setattr(panic_state, "write_panic_state", lambda *a, **k: (_ for _ in ()).throw(OSError("x")))
    steps = engage_order_block("t", "r", panic=True, pause=True, kill_switch_lock=True)
    assert steps == {"panic_flag": "FAILED", "trading_pause": "WRITTEN", "kill_switch_lock": "WRITTEN"}


def test_live_emergency_rollback_does_not_claim_a_flatten(client, control_auth):
    res = client.post("/api/live/emergency-flatten", headers=control_auth)
    assert res.status_code == 200
    body = res.get_json()
    assert "no positions were flattened" in body["message"]
    assert body["steps"]["panic_flag"] == "WRITTEN"
    assert is_panic_active()


# ── smaller handler fixes ─────────────────────────────────────────────────────

def test_advisory_toggle_requires_an_explicit_boolean(client, control_auth):
    for bad in ("yes", 0, None, "maybe", [True]):
        res = client.post("/api/testnet/advisory/toggle", json={"shadow_mode": bad}, headers=control_auth)
        assert res.status_code == 400, bad
    # missing Content-Type is not turned into a 500 any more
    res = client.post("/api/testnet/advisory/toggle", headers=control_auth)
    assert res.status_code != 500


def test_testnet_close_validates_symbol_and_reports_unavailable_exchange(client, control_auth, monkeypatch):
    res = client.post("/api/testnet/positions/close", json={"symbol": {"$ne": 1}}, headers=control_auth)
    assert res.status_code == 400
    monkeypatch.setattr(execution, "get_exchange_client", lambda: None)
    res = client.post("/api/testnet/positions/close", json={"symbol": "BTCUSDT"}, headers=control_auth)
    assert res.status_code == 503 and res.get_json()["error"] == "EXCHANGE_CLIENT_UNAVAILABLE"
    res = client.post("/api/testnet/positions/close-all", headers=control_auth)
    assert res.status_code == 503


def test_strategy_toggle_rejects_non_boolean(client, control_api_auth):
    res = client.post("/api/v1/control/strategy/adx_ema/toggle", json={"enabled": "maybe"}, headers=control_api_auth)
    assert res.status_code == 400 and res.get_json()["error"] == "INVALID_ENABLED"
    res = client.post("/api/v1/control/strategy/adx_ema/toggle", json={"enabled": False}, headers=control_api_auth)
    assert res.status_code == 200


# ── IP abuse block must not lock the operator out of the kill switch ─────────

def test_ip_block_does_not_lock_valid_key_out_of_emergency_controls(client, control_auth):
    """Ten bad guesses from a shared address (every client behind a proxy/NAT
    shares the peer IP) used to lock the real operator out of /api/panic for
    five minutes. A valid key now still reaches emergency endpoints; invalid
    keys and non-emergency endpoints stay blocked."""
    import security_hardening as sh

    for _ in range(12):
        assert client.post("/api/panic", json={}, headers={"X-API-KEY": "wrong-key"}).status_code == 401
    assert sh._security_monitor.is_ip_blocked("127.0.0.1")

    # Non-emergency control endpoint: still blocked even with the valid key.
    res = client.post("/api/testnet/advisory/toggle", json={"enabled": False}, headers=control_auth)
    assert res.status_code == 401
    assert "IP_TEMPORARILY_BLOCKED" in res.get_data(as_text=True)

    # Invalid key on the emergency endpoint: still rejected.
    assert client.post("/api/panic", json={}, headers={"X-API-KEY": "wrong-key"}).status_code == 401
    assert not is_panic_active()

    # Valid key on the emergency endpoint: the kill switch engages.
    res = client.post("/api/panic", json={}, headers=control_auth)
    assert res.status_code == 200, res.get_data(as_text=True)
    assert res.get_json()["panic_active"] is True
    assert is_panic_active()


def test_request_guard_and_auth_share_one_emergency_endpoint_list():
    import security_hardening as sh
    from api import request_guard

    assert request_guard.EMERGENCY_ENDPOINTS is sh.EMERGENCY_ENDPOINTS
    from dashboard import app

    registered = set(app.view_functions)
    missing = sorted(sh.EMERGENCY_ENDPOINTS - registered)
    assert not missing, f"emergency endpoints not registered: {missing}"


@pytest.mark.parametrize("bad_key", [["a"], {"k": 1}, [{"x": 1}], "has spaces", "x" * 200, 12])
def test_friday_task_rejects_non_string_idempotency_keys(client, friday_auth, bad_key):
    """A list/object idempotency key reached a dict lookup and raised
    ``TypeError: unhashable type`` (HTTP 500)."""
    res = client.post("/v1/friday/task", json={"action": "status", "idempotency_key": bad_key}, headers=friday_auth)
    assert res.status_code == 400, res.get_data(as_text=True)[:200]
    assert res.get_json()["field"] == "idempotency_key"


def test_friday_task_accepts_a_valid_idempotency_key_and_replays(client, friday_auth):
    body = {"action": "status", "idempotency_key": "census-key-1"}
    first = client.post("/v1/friday/task", json=body, headers=friday_auth)
    assert first.status_code == 200, first.get_data(as_text=True)[:200]
    second = client.post("/v1/friday/task", json=body, headers=friday_auth)
    assert second.status_code in (200, 409)
