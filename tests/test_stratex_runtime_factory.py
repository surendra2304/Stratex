import pytest

from stratex_agents.runtime import build_order_manager, build_venue_aware_authorizer
from stratex_agents.order_intent import TradeIntent


class FakeVenue:
    def submit_intent(self, intent):
        return {"status": "FILLED", "order_id": "demo-001"}


def base_env(monkeypatch):
    monkeypatch.setenv("TRADING_MODE", "FUTURES")
    monkeypatch.setenv("STRATEX_EXECUTION_MODE", "DEMO")
    monkeypatch.setenv("STRATEX_ALLOWED_EXECUTION_VENUES", "binance")
    monkeypatch.setenv("BINANCE_DEMO_TRADING", "true")
    monkeypatch.setenv("LIVE_TRADING_ENABLED", "false")


def test_runtime_factory_requires_explicit_demo_mode(monkeypatch):
    monkeypatch.setenv("TRADING_MODE", "FUTURES")
    monkeypatch.setenv("STRATEX_EXECUTION_MODE", "DISABLED")
    monkeypatch.setenv("STRATEX_ALLOWED_EXECUTION_VENUES", "binance")
    with pytest.raises(PermissionError, match="STRATEX_EXECUTION_MODE"):
        build_order_manager(lambda _intent: (True, "approved"), binance_adapter=FakeVenue())


def test_runtime_factory_builds_only_allowlisted_venue(monkeypatch, tmp_path):
    base_env(monkeypatch)
    venue = FakeVenue()
    manager = build_order_manager(
        lambda _intent: (True, "approved"),
        binance_adapter=venue,
    )
    assert set(manager.venues) == {"binance"}


def test_runtime_factory_rejects_unknown_venue(monkeypatch):
    base_env(monkeypatch)
    monkeypatch.setenv("STRATEX_ALLOWED_EXECUTION_VENUES", "binance,unknown")
    with pytest.raises(ValueError, match="supports only"):
        build_order_manager(lambda _intent: (True, "approved"), binance_adapter=FakeVenue())


def test_runtime_factory_blocks_live_mode(monkeypatch):
    base_env(monkeypatch)
    monkeypatch.setenv("LIVE_TRADING_ENABLED", "true")
    with pytest.raises(PermissionError, match="Live-money"):
        build_order_manager(lambda _intent: (True, "approved"), binance_adapter=FakeVenue())


def test_runtime_factory_requires_mt5_demo_config(monkeypatch):
    base_env(monkeypatch)
    monkeypatch.setenv("STRATEX_ALLOWED_EXECUTION_VENUES", "mt5")
    monkeypatch.setenv("MT5_BRIDGE_ENABLED", "true")
    monkeypatch.setenv("MT5_REQUIRE_DEMO", "true")
    monkeypatch.setenv("MT5_BRIDGE_URL", "http://100.64.0.2:8765")
    monkeypatch.setenv("MT5_BRIDGE_TOKEN", "demo-bridge-token")
    with pytest.raises(PermissionError, match="TRADING_MODE=FUTURES"):
        monkeypatch.setenv("TRADING_MODE", "TESTNET")
        build_order_manager(lambda _intent: (True, "approved"), mt5_adapter=FakeVenue())



class FakeMT5Adapter(FakeVenue):
    def __init__(self, demo=True):
        self.enabled = True
        self.base_url = "http://100.64.0.2:8765"
        self.token = "private-test-token"
        self.is_connected = False
        self.demo = demo

    def health(self):
        return {
            "demo": self.demo,
            "server": "ICMarketsSC-Demo" if self.demo else "ICMarketsSC-Live",
        }


def configure_mt5_demo(monkeypatch):
    monkeypatch.setenv("TRADING_MODE", "FUTURES")
    monkeypatch.setenv("STRATEX_EXECUTION_MODE", "DEMO")
    monkeypatch.setenv("STRATEX_ALLOWED_EXECUTION_VENUES", "mt5")
    monkeypatch.setenv("LIVE_TRADING_ENABLED", "false")
    monkeypatch.setenv("MT5_BRIDGE_ENABLED", "true")
    monkeypatch.setenv("MT5_REQUIRE_DEMO", "true")
    monkeypatch.setenv("MT5_BRIDGE_URL", "http://100.64.0.2:8765")
    monkeypatch.setenv("MT5_BRIDGE_TOKEN", "private-test-token")


def test_runtime_factory_health_checks_even_an_injected_mt5_adapter(monkeypatch):
    configure_mt5_demo(monkeypatch)
    adapter = FakeMT5Adapter(demo=True)
    manager = build_order_manager(
        lambda _intent: (True, "approved"),
        mt5_adapter=adapter,
        check_mt5_health=True,
    )
    assert set(manager.venues) == {"mt5"}


def test_runtime_factory_refuses_injected_mt5_adapter_without_demo_confirmation(monkeypatch):
    configure_mt5_demo(monkeypatch)
    adapter = FakeMT5Adapter(demo=False)
    with pytest.raises(ConnectionError, match="authenticated demo health check"):
        build_order_manager(
            lambda _intent: (True, "approved"),
            mt5_adapter=adapter,
            check_mt5_health=True,
        )



class FakeMT5RiskAgent:
    def evaluate_intent_risk(self, intent, context):
        assert intent.venue == "mt5"
        assert context == {"fresh": True}
        return True, "MT5_BROKER_RISK_OK", "demo sizing verified"


def test_venue_aware_authorizer_uses_mt5_broker_risk_agent():
    authorizer = build_venue_aware_authorizer(
        execution_policy=lambda: (True, "DEMO_ALLOWED"),
        context_provider=lambda _intent: {"fresh": True},
        mt5_agent=FakeMT5RiskAgent(),
    )
    intent = TradeIntent(
        venue="mt5", symbol="XAUUSD.a", side="BUY", order_type="MARKET",
        quantity=0.1, environment="mt5_demo", stop_loss=2290, take_profit=2320,
        strategy="authorizer-test", client_order_id="stx-auth-mt5-001",
    )
    allowed, reason = authorizer(intent)
    assert allowed is True
    assert reason == "MT5_BROKER_RISK_OK: demo sizing verified"


def test_venue_aware_authorizer_fails_closed_when_context_provider_raises():
    authorizer = build_venue_aware_authorizer(
        execution_policy=lambda: (True, "DEMO_ALLOWED"),
        context_provider=lambda _intent: (_ for _ in ()).throw(TimeoutError()),
    )
    intent = TradeIntent(
        venue="mt5", symbol="XAUUSD.a", side="BUY", order_type="MARKET",
        quantity=0.1, environment="mt5_demo", stop_loss=2290, take_profit=2320,
        strategy="authorizer-test", client_order_id="stx-auth-mt5-002",
    )
    allowed, reason = authorizer(intent)
    assert allowed is False
    assert reason == "RISK_CONTEXT_PROVIDER_ERROR: TimeoutError"
