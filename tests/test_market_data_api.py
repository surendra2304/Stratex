from flask import Flask

from dashboard import (
    api_v1_market_candles,
    api_v1_market_instruments,
    api_v1_market_quote,
)
from stratex_agents.market_data import MarketDataAgent


class FakeMarketDataAgent:
    def list_instruments(self, **kwargs):
        return {
            "instruments": [{
                "venue": "mt5", "environment": "mt5_demo", "symbol": "XAUUSD.a",
                "asset_class": "commodity_cfd", "volume_step": 0.01,
            }],
            "providers": [{"venue": "mt5", "status": "ok", "count": 1}],
            "partial": False, "count": 1,
        }

    def get_quote(self, venue, symbol):
        return {
            "venue": venue, "environment": "mt5_demo", "symbol": symbol,
            "bid": 2300.1, "ask": 2300.3, "last": 2300.2, "spread": 0.2,
            "received_at": 1700000000.0, "source_timestamp_ms": 1700000000000,
        }

    def get_candles(self, venue, symbol, timeframe, limit):
        return [{
            "venue": venue, "environment": "mt5_demo", "symbol": symbol,
            "timeframe": timeframe, "timestamp_ms": 1700000000000,
            "open": 2300, "high": 2301, "low": 2299, "close": 2300.5, "volume": 15,
        }]


def _call_unwrapped(view, url):
    from dashboard import app
    with app.test_request_context(url):
        return app.make_response(view.__wrapped__())


def test_market_instruments_endpoint_uses_validated_filters(monkeypatch):
    monkeypatch.setattr(
        MarketDataAgent, "from_environment",
        classmethod(lambda cls: FakeMarketDataAgent()),
    )
    response = _call_unwrapped(
        api_v1_market_instruments,
        "/api/v1/market/instruments?venue=mt5&query=gold&asset_class=commodity_cfd&visible_only=true&limit=20",
    )
    payload = response.get_json()
    assert response.status_code == 200
    assert payload["count"] == 1
    assert payload["instruments"][0]["symbol"] == "XAUUSD.a"


def test_market_quote_endpoint_routes_to_explicit_mt5_venue(monkeypatch):
    monkeypatch.setattr(
        MarketDataAgent, "from_environment",
        classmethod(lambda cls: FakeMarketDataAgent()),
    )
    response = _call_unwrapped(
        api_v1_market_quote,
        "/api/v1/market/quote?venue=mt5&symbol=XAUUSD.a",
    )
    payload = response.get_json()
    assert response.status_code == 200
    assert payload["quote"]["venue"] == "mt5"
    assert payload["quote"]["symbol"] == "XAUUSD.a"
    assert payload["quote"]["last"] == 2300.2


def test_market_candles_endpoint_normalizes_mt5_ohlcv(monkeypatch):
    monkeypatch.setattr(
        MarketDataAgent, "from_environment",
        classmethod(lambda cls: FakeMarketDataAgent()),
    )
    response = _call_unwrapped(
        api_v1_market_candles,
        "/api/v1/market/candles?venue=mt5&symbol=XAUUSD.a&timeframe=15m&limit=20",
    )
    payload = response.get_json()
    assert response.status_code == 200
    assert payload["count"] == 1
    assert payload["candles"][0]["venue"] == "mt5"
    assert payload["candles"][0]["timestamp_ms"] == 1700000000000


def test_market_quote_endpoint_rejects_broker_symbol_with_invalid_characters():
    from api.validation import RequestValidationError
    from dashboard import app
    with app.test_request_context("/api/v1/market/quote?venue=mt5&symbol=XAUUSD%2F.."):
        try:
            api_v1_market_quote.__wrapped__()
        except RequestValidationError as exc:
            assert exc.field == "symbol"
        else:
            raise AssertionError("unsafe broker symbol should be rejected before adapter routing")
