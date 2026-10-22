import pytest

from stratex_agents.market_data import MarketDataAgent, MarketDataUnavailable


class FakeBinanceProvider:
    environment = "binance_demo"

    def list_instruments(self, query="", limit=1000):
        rows = [
            {
                "venue": "binance", "environment": "binance_demo", "symbol": "BTCUSDT",
                "description": "BTCUSDT BTC USDT", "path": "Crypto/Perpetual Futures",
                "asset_class": "crypto_perpetual", "base_currency": "BTC",
                "quote_currency": "USDT", "margin_currency": "USDT", "contract_size": 1,
                "digits": 1, "tick_size": 0.1, "volume_min": 0.001, "volume_max": 100,
                "volume_step": 0.001, "min_notional": 5, "tradeable": True, "visible": True,
            }
        ]
        if query and query.casefold() not in rows[0]["description"].casefold():
            return []
        return rows[:limit]

    def get_quote(self, symbol):
        return {
            "venue": "binance", "environment": "binance_demo", "symbol": symbol.upper(),
            "bid": 99.9, "ask": 100.1, "last": 100.0, "spread": 0.2,
            "timestamp_ms": 1700000000000,
        }

    def get_candles(self, symbol, timeframe="15m", limit=300):
        return [{
            "venue": "binance", "environment": "binance_demo", "symbol": symbol.upper(),
            "timeframe": timeframe, "timestamp_ms": 1700000000000,
            "open": 99, "high": 101, "low": 98, "close": 100, "volume": 123,
        }]


class FakeMT5Provider:
    is_connected = True

    def get_symbols(self, query="", visible_only=False, limit=1000):
        rows = [
            {
                "symbol": "XAUUSD.a", "description": "Gold vs US Dollar",
                "path": "Metals\\Gold", "visible": True, "trade_mode": 4,
                "base_currency": "XAU", "profit_currency": "USD", "margin_currency": "USD",
                "contract_size": 100, "digits": 2, "tick_size": 0.01, "tick_value": 1.0,
                "volume_min": 0.01, "volume_max": 50, "volume_step": 0.01,
                "stops_level_points": 50,
            },
            {
                "symbol": "EURUSD", "description": "Euro vs US Dollar",
                "path": "Forex\\Majors", "visible": True, "trade_mode": 4,
                "base_currency": "EUR", "profit_currency": "USD", "margin_currency": "USD",
                "contract_size": 100000, "digits": 5, "tick_size": 0.00001, "tick_value": 1.0,
                "volume_min": 0.01, "volume_max": 100, "volume_step": 0.01,
            },
        ]
        if query:
            rows = [row for row in rows if query.casefold() in
                    " ".join((row["symbol"], row["description"], row["path"])).casefold()]
        return rows[:limit]

    def get_ticker(self, symbol):
        return type("Ticker", (), {
            "symbol": symbol, "bid": 1.1, "ask": 1.1002, "last": 1.1001,
        })()

    def get_historical_data(self, symbol, timeframe="15m", limit=300):
        return [{
            "symbol": symbol, "timeframe": timeframe, "timestamp": 1700000000,
            "open": 1.1, "high": 1.11, "low": 1.09, "close": 1.105, "volume": 500,
        }]


def test_market_data_agent_preserves_native_symbols_and_distinguishes_asset_classes():
    agent = MarketDataAgent({"binance": FakeBinanceProvider(), "mt5": FakeMT5Provider()})
    result = agent.list_instruments()
    rows = result["instruments"]
    assert result["partial"] is False
    assert any(row["symbol"] == "BTCUSDT" and row["asset_class"] == "crypto_perpetual" for row in rows)
    assert any(row["symbol"] == "XAUUSD.a" and row["asset_class"] == "commodity_cfd" for row in rows)
    assert any(row["symbol"] == "EURUSD" and row["asset_class"] == "forex" for row in rows)
    assert next(row for row in rows if row["symbol"] == "XAUUSD.a")["quantity_step"] == 0.01


def test_market_data_agent_can_filter_by_venue_asset_class_and_query():
    agent = MarketDataAgent({"binance": FakeBinanceProvider(), "mt5": FakeMT5Provider()})
    result = agent.list_instruments(venue="mt5", asset_class="commodity_cfd", query="gold")
    assert result["count"] == 1
    assert result["instruments"][0]["symbol"] == "XAUUSD.a"
    assert result["providers"] == [{"venue": "mt5", "status": "ok", "count": 1}]


def test_market_data_agent_never_falls_back_between_venues():
    agent = MarketDataAgent({"binance": FakeBinanceProvider()})
    with pytest.raises(MarketDataUnavailable, match="not configured"):
        agent.get_quote("mt5", "EURUSD")
    with pytest.raises(MarketDataUnavailable, match="not configured"):
        agent.get_candles("mt5", "EURUSD")


def test_market_data_agent_normalizes_quote_and_candles_per_venue():
    agent = MarketDataAgent({"binance": FakeBinanceProvider(), "mt5": FakeMT5Provider()})
    mt5_quote = agent.get_quote("mt5", "EURUSD")
    assert mt5_quote["venue"] == "mt5"
    assert mt5_quote["environment"] == "mt5_demo"
    assert mt5_quote["spread"] == pytest.approx(0.0002)
    candle = agent.get_candles("mt5", "EURUSD")[0]
    assert candle["timestamp_ms"] == 1700000000000
    assert candle["venue"] == "mt5"
    assert candle["environment"] == "mt5_demo"


def test_market_data_agent_reports_partial_provider_failures_explicitly():
    class BrokenProvider:
        def get_symbols(self, **kwargs):
            raise TimeoutError("offline")

        def list_instruments(self, **kwargs):
            raise TimeoutError("offline")

    agent = MarketDataAgent({"binance": FakeBinanceProvider(), "mt5": BrokenProvider()})
    result = agent.list_instruments()
    assert result["partial"] is True
    assert result["providers"][1] == {
        "venue": "mt5", "status": "unavailable", "error": "TimeoutError"
    }
    assert len(result["instruments"]) == 1
