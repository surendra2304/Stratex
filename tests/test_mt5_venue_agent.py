import pytest
from stratex_agents.venue.mt5_agent import MT5VenueAgent
from stratex_agents.order_intent import TradeIntent
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


class FakeMT5RiskAdapter(FakeMT5Adapter):
    def __init__(self, sizing=None, error=None):
        super().__init__()
        self.sizing = sizing or {
            "approved": True, "recommended_volume": 0.2, "volume_min": 0.01,
            "volume_step": 0.01, "risk_amount": 2.0, "account_currency": "USD",
            "risk_budget": 5.0, "margin_required": 100.0, "margin_budget": 200.0,
        }
        self.sizing_error = error
        self.sizing_calls = []

    def calculate_position_size(self, symbol, side, stop_loss, take_profit):
        self.sizing_calls.append((symbol, side, stop_loss, take_profit))
        if self.sizing_error:
            raise self.sizing_error
        return self.sizing


def mt5_demo_intent(quantity=0.1):
    return TradeIntent(
        venue="mt5", symbol="XAUUSD.a", side="BUY", order_type="MARKET",
        quantity=quantity, environment="mt5_demo", stop_loss=2290, take_profit=2320,
        strategy="risk-test", client_order_id="stx-mt5-risk-001",
    )


def test_mt5_agent_uses_broker_calculated_pnl_and_margin_before_authorizing(monkeypatch):
    monkeypatch.setenv("MT5_REQUIRE_DEMO", "true")
    adapter = FakeMT5RiskAdapter()
    decision = MT5VenueAgent(adapter).evaluate_intent_risk(mt5_demo_intent(), context={})
    assert decision == (
        True, "MT5_BROKER_RISK_OK",
        "risk=2.00 USD; risk_budget=5.00; margin=100.00; margin_budget=200.00; recommended_volume=0.2",
    )
    assert adapter.sizing_calls == [("XAUUSD.a", "BUY", 2290, 2320)]


def test_mt5_agent_denies_requested_volume_above_broker_sized_limit(monkeypatch):
    monkeypatch.setenv("MT5_REQUIRE_DEMO", "true")
    decision = MT5VenueAgent(FakeMT5RiskAdapter()).evaluate_intent_risk(mt5_demo_intent(quantity=0.3), context={})
    assert decision[0] is False
    assert decision[1] == "MT5_RISK_OR_MARGIN_LIMIT"


def test_mt5_agent_fails_closed_when_broker_risk_calculation_is_unavailable(monkeypatch):
    monkeypatch.setenv("MT5_REQUIRE_DEMO", "true")
    decision = MT5VenueAgent(FakeMT5RiskAdapter(error=TimeoutError("terminal unavailable"))).evaluate_intent_risk(
        mt5_demo_intent(), context={}
    )
    assert decision == (False, "MT5_BROKER_RISK_UNAVAILABLE", "TimeoutError")
