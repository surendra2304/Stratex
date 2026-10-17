import pytest

import stratex_ccxt_adapter.client as ccxt_client


class FakeExchange:
    def __init__(self, config):
        self.config = config
        self.demo_enabled = False
        self.sandbox_enabled = False

    def enable_demo_trading(self, enabled=True):
        self.demo_enabled = enabled

    def set_sandbox_mode(self, enabled=True):
        self.sandbox_enabled = enabled


class FakeCCXT:
    exchanges = ["binance"]
    binance = FakeExchange


def test_binance_demo_trading_uses_current_demo_mode(monkeypatch):
    monkeypatch.setattr(ccxt_client, "ccxt", FakeCCXT)
    adapter = ccxt_client.CCXTExchangeAdapter(
        "binance", sandbox=False, demo_trading=True, api_key="demo-key", secret="demo-secret"
    )
    assert adapter.exchange.demo_enabled is True
    assert adapter.exchange.sandbox_enabled is False


def test_binance_demo_mode_fails_closed_when_ccxt_is_too_old(monkeypatch):
    class OldExchange:
        def __init__(self, config):
            self.config = config

    class OldCCXT:
        exchanges = ["binance"]
        binance = OldExchange

    monkeypatch.setattr(ccxt_client, "ccxt", OldCCXT)
    with pytest.raises(RuntimeError, match="ccxt>=4.5.6"):
        ccxt_client.CCXTExchangeAdapter("binance", sandbox=False, demo_trading=True)
