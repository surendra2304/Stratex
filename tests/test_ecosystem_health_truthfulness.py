"""Truthfulness contract for the master ecosystem health endpoint."""
from types import SimpleNamespace


def test_health_reports_measured_local_components_and_unknown_external_health(monkeypatch):
    import api.master_control_api as master_control
    from dashboard import app

    fake_director = SimpleNamespace(
        autonomy_level=2,
        decision_log=[object(), object()],
        self_healing=SimpleNamespace(
            healed_incidents_count=7,
            strategy_crash_restarts={"adx_ema": 2},
        ),
        state_machine=SimpleNamespace(
            get_state_summary=lambda: {"current_state": "PROTECTED", "recent_transitions": []}
        ),
        degradation=object(),
    )
    monkeypatch.setattr(master_control, "_director", fake_director)

    with app.test_client() as client:
        response = client.get("/api/ecosystem/health")

    assert response.status_code == 200
    health = response.get_json()["data"]
    assert health["overall_status"] == "PARTIAL"
    assert health["health_scope"] == "PROCESS_LOCAL_AUTONOMY_OBJECTS_ONLY"
    assert set(health["local_components"].values()) == {"INITIALIZED"}
    assert health["operations_director"]["autonomy_level"] == 2
    assert health["operations_director"]["decisions_count"] == 2
    assert health["self_healing"]["healed_incidents"] == 7
    assert health["state_machine"]["current_state"] == "PROTECTED"
    assert health["state_machine_scope"] == "IN_PROCESS_CONTROL_POSTURE_ONLY_NOT_EXTERNAL_HEALTH_VERIFICATION"
    assert "exchange_connectivity" in health["unverified_subsystems"]
    assert "market_data_freshness" in health["unverified_subsystems"]
    assert "HEALTHY" not in str(health)
    assert "OPERATIONAL" not in str(health)


def test_health_degrades_when_local_director_is_unavailable(monkeypatch):
    import api.master_control_api as master_control
    from dashboard import app

    monkeypatch.setattr(master_control, "_director", None)
    with app.test_client() as client:
        response = client.get("/api/ecosystem/health")

    assert response.status_code == 200
    health = response.get_json()["data"]
    assert health["overall_status"] == "DEGRADED"
    assert health["local_components"]["operations_director"] == "UNAVAILABLE"
    assert health["operations_director"]["autonomy_level"] is None
    assert "HEALTHY" not in str(health)


def test_ecosystem_mode_requires_explicit_confirmation(monkeypatch):
    from dashboard import app

    api_key = "ecosystem-mode-confirmation-test-key-123456"
    monkeypatch.setenv("BOT_API_KEY", api_key)
    monkeypatch.setenv("ENVIRONMENT", "development")

    with app.test_client() as client:
        response = client.post(
            "/api/ecosystem/mode",
            json={"level": 2},
            headers={"X-API-Key": api_key},
        )

    assert response.status_code == 400
    assert response.get_json()["error"] == "INVALID_OR_UNCONFIRMED"


def test_master_ecosystem_routes_are_registered_once():
    from dashboard import app

    expected = (
        "/api/ecosystem/status",
        "/api/ecosystem/decisions",
        "/api/ecosystem/health",
        "/api/ecosystem/mode",
        "/api/ecosystem/report",
    )
    for path in expected:
        rules = [rule for rule in app.url_map.iter_rules() if str(rule.rule) == path]
        assert len(rules) == 1, f"Expected one authoritative handler for {path}, got {len(rules)}"
