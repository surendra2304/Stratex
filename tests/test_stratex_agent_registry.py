from stratex_agents.registry import get_agent_catalog


def test_agent_catalog_contains_expected_roles(monkeypatch):
    monkeypatch.setenv("TRADING_MODE", "PAPER")
    monkeypatch.setenv("BINANCE_DEMO_TRADING", "true")
    monkeypatch.setenv("BINANCE_API_KEY", "demo-key")
    monkeypatch.setenv("BINANCE_API_SECRET", "demo-secret")
    monkeypatch.setenv("MT5_BRIDGE_ENABLED", "true")
    monkeypatch.setenv("MT5_BRIDGE_URL", "http://100.64.0.2:8765")
    monkeypatch.setenv("MT5_BRIDGE_TOKEN", "secret-token")
    catalog = {agent["id"]: agent for agent in get_agent_catalog()}

    assert {"stratex_core", "market_data", "strategy_supervisor", "risk_guard",
            "binance_futures", "mt5", "portfolio", "reconciliation",
            "inference_advisor", "futuris_forecast", "intelx_research",
            "audit_monitor"} <= set(catalog)
    assert catalog["binance_futures"]["status"] == "configured_demo"
    assert catalog["mt5"]["status"] == "configured_pending_health_check"
    assert all(agent["execution_authority"] is False for agent in catalog.values())
    assert all(agent["live_trading_enabled"] is False for agent in catalog.values())
    assert "secret-token" not in str(catalog)


def test_new_venues_remain_disabled_without_explicit_configuration(monkeypatch):
    monkeypatch.delenv("BINANCE_DEMO_TRADING", raising=False)
    monkeypatch.delenv("BINANCE_API_KEY", raising=False)
    monkeypatch.delenv("BINANCE_API_SECRET", raising=False)
    monkeypatch.delenv("MT5_BRIDGE_ENABLED", raising=False)
    monkeypatch.delenv("MT5_BRIDGE_URL", raising=False)
    monkeypatch.delenv("MT5_BRIDGE_TOKEN", raising=False)
    catalog = {agent["id"]: agent for agent in get_agent_catalog()}
    assert catalog["mt5"]["status"] == "disabled"
