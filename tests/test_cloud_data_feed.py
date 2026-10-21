"""
tests/test_cloud_data_feed.py — Unit tests for Resilient Cloud Market Data Feed.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from autonomy.cloud_data_feed import (
    SYMBOL_MAP,
    TIMEFRAME_MAP,
    CloudMarketDataFeed,
    get_cloud_data_feed,
)


class TestCloudMarketDataFeed:
    def test_resolve_ticker_known_and_unknown(self):
        feed = CloudMarketDataFeed()
        assert feed.resolve_ticker("EURUSD") == "EURUSD=X"
        assert feed.resolve_ticker("XAUUSD") == "GC=F"
        assert feed.resolve_ticker("GOLD") == "GC=F"
        assert feed.resolve_ticker("WTI") == "CL=F"
        assert feed.resolve_ticker("BTCUSD") == "BTC-USD"
        assert feed.resolve_ticker("UNKNOWN") == "UNKNOWN=X"

    @patch("requests.Session.get")
    def test_get_candles_success(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "chart": {
                "result": [
                    {
                        "timestamp": [1700000000, 1700000900],
                        "indicators": {
                            "quote": [
                                {
                                    "open": [1.0850, 1.0860],
                                    "high": [1.0870, 1.0880],
                                    "low": [1.0840, 1.0855],
                                    "close": [1.0865, 1.0875],
                                    "volume": [1200.0, 1500.0],
                                }
                            ]
                        },
                    }
                ]
            }
        }
        mock_get.return_value = mock_resp

        feed = CloudMarketDataFeed()
        candles = feed.get_candles("EURUSD", timeframe="15m", limit=10)
        assert len(candles) == 2
        assert candles[0]["open"] == 1.0850
        assert candles[1]["close"] == 1.0875
        assert candles[1]["volume"] == 1500.0

    @patch("requests.Session.get")
    def test_get_candles_http_failure(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.status_code = 404
        mock_get.return_value = mock_resp

        feed = CloudMarketDataFeed()
        candles = feed.get_candles("EURUSD")
        assert candles == []

    @patch("requests.Session.get")
    def test_get_candles_exception_handling(self, mock_get):
        mock_get.side_effect = RuntimeError("Network partition")
        feed = CloudMarketDataFeed()
        candles = feed.get_candles("EURUSD")
        assert candles == []

    @patch.object(CloudMarketDataFeed, "get_candles")
    def test_get_current_price(self, mock_candles):
        mock_candles.return_value = [
            {"timestamp": 1700000000, "close": 2655.50}
        ]
        feed = CloudMarketDataFeed()
        price = feed.get_current_price("XAUUSD")
        assert price == 2655.50

    @patch.object(CloudMarketDataFeed, "get_candles", return_value=[])
    def test_get_current_price_none_when_empty(self, mock_candles):
        feed = CloudMarketDataFeed()
        price = feed.get_current_price("XAUUSD")
        assert price is None

    def test_singleton_accessor(self):
        f1 = get_cloud_data_feed()
        f2 = get_cloud_data_feed()
        assert f1 is f2
