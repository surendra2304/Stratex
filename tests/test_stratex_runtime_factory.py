import pytest

from stratex_agents.runtime import build_order_manager


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
