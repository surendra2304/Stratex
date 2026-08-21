from unittest.mock import MagicMock, patch

import pytest

from data_client import MarketDataClient


def test_market_data_client_no_execution_methods():
    """
    Proves that MarketDataClient does not expose execution capabilities
    and accurately raises AttributeError when attempted.
    """
    with patch("data_client.Client"):
        # We need TRADING_MODE != PAPER to allow initialization
        with patch("data_client.TRADING_MODE", "RESEARCH"):
            client = MarketDataClient()
            
            # Check valid methods
            assert hasattr(client, "get_ticker")
            assert hasattr(client, "get_historical_klines")
            assert hasattr(client, "futures_funding_rate")
            
            # Check blocked execution methods
            with pytest.raises(AttributeError, match="MarketDataClient strictly prohibits"):
                client.create_order(symbol="BTCUSDT", side="BUY", type="MARKET", quantity=1)
                
            with pytest.raises(AttributeError, match="MarketDataClient strictly prohibits"):
                client.cancel_order(symbol="BTCUSDT", orderId=123)
                
            with pytest.raises(AttributeError, match="MarketDataClient strictly prohibits"):
                client.withdraw(asset="USDT", address="0x123", amount=100)

def test_market_data_client_paper_mode():
    """
    Proves that PAPER mode safely disables the raw Binance Client and returns DATA_UNAVAILABLE.
    """
    with patch("data_client.TRADING_MODE", "PAPER"):
        client = MarketDataClient()
        assert client.is_available() is False
        assert client.data_source == "DATA_UNAVAILABLE"
        
        # When unavailable, reads return None
        assert client.get_ticker() is None
        assert client.get_historical_klines("BTCUSDT", "1m", "1 day ago UTC") is None

def test_market_data_client_research_mode():
    """
    Proves that RESEARCH mode is ALLOWED to fetch data.
    """
    with patch("data_client.TRADING_MODE", "RESEARCH"), \
         patch("data_client.Client") as mock_client_class:
         
        # Mock instance
        mock_instance = MagicMock()
        mock_instance.get_ticker.return_value = [{"symbol": "BTCUSDT", "price": "50000"}]
        mock_client_class.return_value = mock_instance
        
        client = MarketDataClient()
        assert client.is_available() is True
        assert client.data_source == "BINANCE_TESTNET_READ_ONLY"
        
        # Verify read operations pass through
        res = client.get_ticker()
        assert res[0]["price"] == "50000"
        mock_instance.get_ticker.assert_called_once()
