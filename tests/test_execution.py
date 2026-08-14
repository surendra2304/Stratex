import pytest
from unittest.mock import patch, mock_open
import execution
import json

def test_get_open_orders():
    """Test that local counting of open trades works based on active_trades.json."""
    mock_data = [
        {"symbol": "BTCUSDT", "side": "BUY"},
        {"symbol": "ETHUSDT", "side": "BUY"},
        {"symbol": "BTCUSDT", "side": "SELL"},
    ]
    
    with patch("execution._load_active_trades", return_value=mock_data):
        count_btc = execution.get_open_orders("BTCUSDT")
        count_eth = execution.get_open_orders("ETHUSDT")
        count_sol = execution.get_open_orders("SOLUSDT")
        
        assert count_btc == 2
        assert count_eth == 1
        assert count_sol == 0

@patch("execution.TRADING_MODE", "TESTNET")
@patch("execution.client")
@patch("execution._save_active_trades")
@patch("execution._load_active_trades", return_value=[])
@patch("execution.log_trade")
def test_place_market_order_success(mock_log, mock_load, mock_save, mock_client):
    """Test that placing a market order and OCO order successfully returns the order."""
    
    # Mock Market Order
    mock_client.create_order.return_value = {
        "orderId": 123,
        "fills": [{"price": "50000.0"}]
    }
    
    # Mock OCO Order
    mock_client.create_oco_order.return_value = {
        "orderListId": 456
    }
    
    order = execution.place_market_order("scalper", "BUY", "BTCUSDT", 1.0, sl=49000, tp=51000)
    
    assert order is not None
    assert mock_client.create_order.called
    assert mock_client.create_oco_order.called
    
    # Verify save was called to track the new OCO
    assert mock_save.called
    saved_data = mock_save.call_args[0][0]
    assert len(saved_data) == 1
    assert saved_data[0]["oco_id"] == 456
