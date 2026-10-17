import pytest

import stratex_agents.venue.binance_futures_agent as binance_agent


class FakeClient:
    instances = []

    def __init__(self, api_key, api_secret, *, testnet, ping):
        self.api_key = api_key
        self.api_secret = api_secret
        self.testnet = testnet
        self.ping = ping
        self.API_URL = "https://placeholder.invalid/api"
        self.FUTURES_URL = "https://placeholder.invalid/fapi"
        self._request = lambda *args, **kwargs: {}
        FakeClient.instances.append(self)


@pytest.fixture(autouse=True)
def reset_clients():
    FakeClient.instances.clear()


def test_demo_mode_uses_only_new_demo_credentials_and_demo_endpoints(monkeypatch):
    monkeypatch.setattr(binance_agent, "Client", FakeClient)
    monkeypatch.setenv("BINANCE_DEMO_TRADING", "true")
    monkeypatch.setenv("BINANCE_API_KEY", "new-demo-key")
    monkeypatch.setenv("BINANCE_API_SECRET", "new-demo-secret")
    # Old keys must not silently override or replace the new demo keys.
    monkeypatch.setenv("API_KEY", "legacy-testnet-key")
    monkeypatch.setenv("SECRET_KEY", "legacy-testnet-secret")
    monkeypatch.setenv("LIVE_TRADING_ENABLED", "false")

    client = binance_agent.BinanceFuturesAgent().create_client(
        "FUTURES", legacy_api_key="legacy-testnet-key", legacy_secret_key="legacy-testnet-secret"
    )

    assert client.api_key == "new-demo-key"
    assert client.api_secret == "new-demo-secret"
    assert client.testnet is False
    assert client.API_URL == binance_agent.BINANCE_DEMO_SPOT_API_URL
    assert client.FUTURES_URL == binance_agent.BINANCE_DEMO_FUTURES_API_URL
    assert client.stratex_environment == "demo_trading"


def test_demo_mode_fails_closed_instead_of_using_legacy_credentials(monkeypatch):
    monkeypatch.setattr(binance_agent, "Client", FakeClient)
    monkeypatch.setenv("BINANCE_DEMO_TRADING", "true")
    monkeypatch.delenv("BINANCE_API_KEY", raising=False)
    monkeypatch.delenv("BINANCE_API_SECRET", raising=False)
    monkeypatch.setenv("API_KEY", "legacy-testnet-key")
    monkeypatch.setenv("SECRET_KEY", "legacy-testnet-secret")
    monkeypatch.setenv("LIVE_TRADING_ENABLED", "false")

    with pytest.raises(RuntimeError, match="fresh Demo Trading credentials"):
        binance_agent.BinanceFuturesAgent().create_client(
            "FUTURES", legacy_api_key="legacy-testnet-key", legacy_secret_key="legacy-testnet-secret"
        )


def test_legacy_mode_remains_on_legacy_testnet_endpoints(monkeypatch):
    monkeypatch.setattr(binance_agent, "Client", FakeClient)
    monkeypatch.setenv("BINANCE_DEMO_TRADING", "false")
    monkeypatch.setenv("LIVE_TRADING_ENABLED", "false")
    client = binance_agent.BinanceFuturesAgent().create_client(
        "FUTURES", legacy_api_key="legacy-key", legacy_secret_key="legacy-secret"
    )
    assert client.testnet is True
    assert client.FUTURES_URL == binance_agent.BINANCE_LEGACY_FUTURES_TESTNET_URL
    assert client.stratex_environment == "legacy_futures_testnet"


def test_live_mode_fails_closed(monkeypatch):
    monkeypatch.setattr(binance_agent, "Client", FakeClient)
    monkeypatch.setenv("BINANCE_DEMO_TRADING", "false")
    monkeypatch.setenv("LIVE_TRADING_ENABLED", "true")
    with pytest.raises(RuntimeError, match="LIVE_TRADING_ENABLED"):
        binance_agent.BinanceFuturesAgent().create_client("FUTURES", legacy_api_key="k", legacy_secret_key="s")
