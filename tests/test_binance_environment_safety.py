import pytest

import config


def test_config_safety_allows_current_binance_demo_hosts(monkeypatch):
    monkeypatch.setenv("BASE_URL", "https://demo-api.binance.com/api")
    monkeypatch.setenv("FUTURES_BASE_URL", "https://demo-fapi.binance.com/fapi")
    config.validate_environment_safety()


@pytest.mark.parametrize("url_name,url", [
    ("BASE_URL", "https://api.binance.com/api"),
    ("FUTURES_BASE_URL", "https://fapi.binance.com/fapi"),
])
def test_config_safety_rejects_binance_production_hosts(monkeypatch, url_name, url):
    monkeypatch.setenv(url_name, url)
    with pytest.raises(config.SecurityConfigurationError, match="Production Binance URL"):
        config.validate_environment_safety()
