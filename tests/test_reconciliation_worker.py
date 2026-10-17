import pytest

from scripts.reconciliation_worker import reconcile_worker_configuration_error


def demo_binance_env(monkeypatch):
    monkeypatch.setenv("STRATEX_RECONCILIATION_ENABLED", "true")
    monkeypatch.setenv("STRATEX_ORDER_MANAGER_ENABLED", "true")
    monkeypatch.setenv("STRATEX_EXECUTION_MODE", "DEMO")
    monkeypatch.setenv("TRADING_MODE", "FUTURES")
    monkeypatch.setenv("LIVE_TRADING_ENABLED", "false")
    monkeypatch.setenv("STRATEX_ALLOWED_EXECUTION_VENUES", "binance")
    monkeypatch.setenv("TESTNET_ENABLED", "true")


def test_reconciliation_worker_is_disabled_by_default(monkeypatch):
    monkeypatch.delenv("STRATEX_RECONCILIATION_ENABLED", raising=False)
    monkeypatch.delenv("STRATEX_ORDER_MANAGER_ENABLED", raising=False)
    assert "STRATEX_RECONCILIATION_ENABLED" in reconcile_worker_configuration_error()


def test_reconciliation_worker_requires_both_opt_in_flags(monkeypatch):
    demo_binance_env(monkeypatch)
    monkeypatch.setenv("STRATEX_ORDER_MANAGER_ENABLED", "false")
    assert "STRATEX_ORDER_MANAGER_ENABLED" in reconcile_worker_configuration_error()


def test_reconciliation_worker_requires_demo_futures_mode(monkeypatch):
    demo_binance_env(monkeypatch)
    monkeypatch.setenv("TRADING_MODE", "TESTNET")
    assert "TRADING_MODE must be FUTURES" in reconcile_worker_configuration_error()


def test_reconciliation_worker_refuses_live_money_mode(monkeypatch):
    demo_binance_env(monkeypatch)
    monkeypatch.setenv("LIVE_TRADING_ENABLED", "true")
    assert "live-money mode is forbidden" in reconcile_worker_configuration_error()


def test_reconciliation_worker_requires_testnet_gate_for_binance(monkeypatch):
    demo_binance_env(monkeypatch)
    monkeypatch.setenv("TESTNET_ENABLED", "false")
    assert "TESTNET_ENABLED=true" in reconcile_worker_configuration_error()


def test_reconciliation_worker_requires_configured_demo_mt5_when_allowlisted(monkeypatch):
    demo_binance_env(monkeypatch)
    monkeypatch.setenv("STRATEX_ALLOWED_EXECUTION_VENUES", "mt5")
    monkeypatch.setenv("MT5_BRIDGE_ENABLED", "true")
    monkeypatch.setenv("MT5_REQUIRE_DEMO", "true")
    monkeypatch.setenv("MT5_BRIDGE_URL", "http://100.64.0.2:8765")
    monkeypatch.setenv("MT5_BRIDGE_TOKEN", "private-test-token")
    assert reconcile_worker_configuration_error() is None
    monkeypatch.setenv("MT5_REQUIRE_DEMO", "false")
    assert "MT5_REQUIRE_DEMO=true" in reconcile_worker_configuration_error()