import pytest
from stratex_agents.venue.mt5_agent import MT5VenueAgent
from exchanges.mt5_bridge_adapter import MT5BridgeError

class FakeMT5Adapter:
    def __init__(self, enabled=True, base_url="http://100.64.0.2:8765", token="test-secret"):
        self.enabled = enabled
        self.base_url = base_url
        self.token = token
        self.is_connected = False
        self.result = {"demo": True, "server": "ICMarketsSC-Demo"}

    def health(self):
        if isinstance(self.result, Exception):
            raise self.result
        return self.result

def test_mt5_venue_agent_reports_disabled_without_bridge(monkeypatch):
    monkeypatch.setenv("MT5_REQUIRE_DEMO", "true")
    result = MT5VenueAgent(FakeMT5Adapter(enabled=False)).health_check()
    assert result["status"] == "disabled"
    assert result["connected"] is False

def test_mt5_venue_agent_reports_healthy_demo_without_leaking_token(monkeypatch):
    monkeypatch.setenv("MT5_REQUIRE_DEMO", "true")
    result = MT5VenueAgent(FakeMT5Adapter()).health_check()
    assert result["status"] == "healthy"
    assert result["account_mode"] == "demo"
    assert result["server"] == "ICMarketsSC-Demo"
    assert "test-secret" not in str(result)

def test_mt5_venue_agent_fails_closed_on_bridge_error(monkeypatch):
    monkeypatch.setenv("MT5_REQUIRE_DEMO", "true")
    adapter = FakeMT5Adapter()
    adapter.result = MT5BridgeError("bridge unavailable")
    result = MT5VenueAgent(adapter).health_check()
    assert result["status"] == "unhealthy"
    assert result["connected"] is False