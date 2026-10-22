import pytest

from stratex_agents.venue.binance_futures_data_adapter import (
    BinanceFuturesMarketDataAdapter, BinanceMarketDataError,
)


class FakeAgent:
    demo_trading = True

    def create_public_client(self, *, futures=True):
        raise AssertionError("Injected test client should be used")


class FakeBinanceFuturesClient:
    stratex_environment = "demo_trading_public"

    def futures_exchange_info(self):
        return {"symbols": [
            {
                "symbol": "BTCUSDT", "status": "TRADING", "baseAsset": "BTC",
                "quoteAsset": "USDT", "marginAsset": "USDT", "pricePrecision": 1,
                "filters": [
                    {"filterType": "LOT_SIZE", "minQty": "0.001", "maxQty": "1000", "stepSize": "0.001"},
                    {"filterType": "PRICE_FILTER", "tickSize": "0.1"},
                    {"filterType": "MIN_NOTIONAL", "notional": "5"},
                ],
            },
            {
                "symbol": "ETHUSDT", "status": "BREAK", "baseAsset": "ETH",
                "quoteAsset": "USDT", "marginAsset": "USDT", "pricePrecision": 2,
                "filters": [],
            },
        ]}

    def futures_orderbook_ticker(self, symbol):
        return {"symbol": symbol, "bidPrice": "99.9", "askPrice": "100.1"}

    def futures_symbol_ticker(self, symbol):
        return {"symbol": symbol, "price": "100"}

    def futures_klines(self, symbol, interval, limit):
        return [
            [1700000000000, "99", "101", "98", "100", "12.5", 1700000899999],
            [1700000900000, "100", "102", "99", "101", "14.5", 1700001799999],
        ][:limit]


def make_adapter(client=None):
    return BinanceFuturesMarketDataAdapter(
        client=client or FakeBinanceFuturesClient(), venue_agent=FakeAgent()
    )


def test_binance_data_adapter_normalizes_demo_futures_contract_rules():
    adapter = make_adapter()
    instruments = adapter.list_instruments()
    assert len(instruments) == 2
    btc = next(row for row in instruments if row["symbol"] == "BTCUSDT")
    assert btc["environment"] == "binance_demo"
    assert btc["asset_class"] == "crypto_perpetual"
    assert btc["volume_step"] == 0.001
    assert btc["tick_size"] == 0.1
    assert btc["min_notional"] == 5
    assert btc["tradeable"] is True


def test_binance_data_adapter_query_is_case_insensitive():
    adapter = make_adapter()
    assert [row["symbol"] for row in adapter.list_instruments(query="eth")] == ["ETHUSDT"]


def test_binance_data_adapter_normalizes_bid_ask_last():
    quote = make_adapter().get_quote("BTCUSDT")
    assert quote["environment"] == "binance_demo"
    assert quote["bid"] == pytest.approx(99.9)
    assert quote["ask"] == pytest.approx(100.1)
    assert quote["last"] == pytest.approx(100)


def test_binance_data_adapter_normalizes_candle_timestamps_and_ohlcv():
    candles = make_adapter().get_candles("BTCUSDT", timeframe="1m", limit=2)
    assert len(candles) == 2
    assert candles[0]["timestamp_ms"] == 1700000000000
    assert candles[0]["timeframe"] == "1m"
    assert candles[1]["close"] == 101


def test_binance_data_adapter_fails_when_demo_environment_is_mismatched():
    client = FakeBinanceFuturesClient()
    client.stratex_environment = "production"
    with pytest.raises(BinanceMarketDataError, match="environment mismatch"):
        make_adapter(client)
