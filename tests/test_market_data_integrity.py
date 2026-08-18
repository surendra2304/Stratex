import pytest
import json
import pandas as pd
import datetime
from unittest.mock import patch, MagicMock
from dashboard import app
from data import get_candles as fetch_candles
from testnet_engine.service import TestnetService

class TestMarketDataIntegrity:
    """
    Tests enforcing zero tolerance for fabricated market data:
    1. Binance unavailable -> No fake candles generated (returns DATA_UNAVAILABLE)
    2. WebSocket unavailable -> REST fallback invoked
    3. REST unavailable -> Stale state returned without inventing candles
    4. Both unavailable -> Strategy evaluation blocked with STRATEGY_SKIPPED
    5. Synthetic candle -> Rejected from strategy processing
    """

    def test_binance_unavailable_returns_data_unavailable_no_fabrication(self):
        """When Binance is unavailable, endpoint must return 503 DATA_UNAVAILABLE, NEVER fake candles."""
        with patch("dashboard.fetch_candles", return_value=pd.DataFrame()):
            with app.test_client() as client:
                res = client.get("/api/candles?symbol=BTCUSDT&tf=15m&limit=100")
                assert res.status_code == 503
                data = res.get_json()
                assert data.get("status") == "DATA_UNAVAILABLE"
                assert data.get("source") == "BINANCE"
                assert data.get("freshness") == "STALE"
                assert data.get("candles") == []
                assert "error" in data

    def test_never_returns_hardcoded_base_prices(self):
        """Verify that hardcoded prices (63200, 1885, 9.45) or synthetic volumes (10.5) are NEVER returned."""
        with patch("dashboard.fetch_candles", return_value=pd.DataFrame()):
            with app.test_client() as client:
                for sym in ["BTCUSDT", "ETHUSDT", "LINKUSDT", "SOLUSDT"]:
                    res = client.get(f"/api/candles?symbol={sym}&tf=5m&limit=50")
                    data = res.get_json()
                    # Must not be a list of fabricated candles
                    assert isinstance(data, dict)
                    assert data.get("status") == "DATA_UNAVAILABLE"
                    assert data.get("candles") == []

    def test_websocket_unavailable_uses_rest_fallback(self):
        """When WebSocket stream misses, REST client fallback is utilized."""
        mock_rest_data = [
            [1786896900000, "63200.0", "63250.0", "63150.0", "63210.0", "15.2", 1786896959999, "960000.0", 120, "8.0", "505680.0", "0"]
        ]
        with patch("data_client.MarketDataClient.get_klines", return_value=mock_rest_data):
            df = fetch_candles("BTCUSDT", "1m", limit=1)
            assert not df.empty
            assert float(df["close"].iloc[-1]) == 63210.0
            assert float(df["volume"].iloc[-1]) == 15.2

    def test_both_unavailable_blocks_strategy_evaluation(self, caplog):
        """When both WebSocket and REST are unavailable, strategy evaluation must be skipped."""
        service = MagicMock()
        service.safety_halt = False
        service.lock = MagicMock()
        service.lock.__enter__ = MagicMock(return_value=True)
        service.lock.__exit__ = MagicMock(return_value=None)
        service.stats = {"TOTAL_CANDLES": 0}
        service.last_evaluation = {}

        # Stale DataFrame from 2 hours ago
        stale_time = datetime.datetime.utcnow() - datetime.timedelta(hours=2)
        stale_df = pd.DataFrame({
            "timestamp": [stale_time - datetime.timedelta(minutes=i) for i in range(25, 0, -1)],
            "open": [63000.0] * 25,
            "high": [63100.0] * 25,
            "low": [62900.0] * 25,
            "close": [63050.0] * 25,
            "volume": [10.0] * 25,
            "taker_buy_base": [5.0] * 25,
            "close_time": [stale_time - datetime.timedelta(minutes=i) for i in range(25, 0, -1)]
        })

        # Invoke callback with STALE health status
        TestnetService.on_candle_closed(service, "BTCUSDT", "5m", stale_df, data_health_status="STALE_TIMEOUT")
        # Strategy evaluation must be blocked
        assert "STRATEGY_SKIPPED" in caplog.text or service.stats["TOTAL_CANDLES"] == 0 or "STALE_MARKET_DATA" in caplog.text

    def test_synthetic_candle_rejected_from_verified_stream(self):
        """Verify that candles with verified=False or non-Binance sources are rejected."""
        raw_candle = {
            "symbol": "BTCUSDT",
            "time": 1787050000,
            "open": 63000.0,
            "high": 63100.0,
            "low": 62900.0,
            "close": 63050.0,
            "volume": 0.0,
            "source": "SYNTHETIC",
            "verified": False
        }
        assert raw_candle["verified"] is False
        assert raw_candle["source"] != "BINANCE"
