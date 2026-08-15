import pytest
from unittest.mock import patch, mock_open, MagicMock
import execution
import json
import math
from paper_engine.exceptions import StateCorruptionError

def test_get_open_orders():
    """Test that local counting of open trades works based on active_trades.json."""
    mock_data = [
        {"strategy": "a", "symbol": "BTCUSDT", "side": "BUY", "quantity": 1, "entry_price": 1000, "oco_id": 1, "tp_price": 2000, "sl_price": 500},
        {"strategy": "a", "symbol": "ETHUSDT", "side": "BUY", "quantity": 1, "entry_price": 1000, "oco_id": 2, "tp_price": 2000, "sl_price": 500},
        {"strategy": "a", "symbol": "BTCUSDT", "side": "SELL", "quantity": 1, "entry_price": 1000, "oco_id": 3, "tp_price": 500, "sl_price": 2000},
    ]
    
    with patch("execution._load_active_trades", return_value=mock_data):
        count_btc = execution.get_open_orders("BTCUSDT")
        count_eth = execution.get_open_orders("ETHUSDT")
        count_sol = execution.get_open_orders("SOLUSDT")
        
        assert count_btc == 2
        assert count_eth == 1
        assert count_sol == 0

def test_get_open_orders_propagates_corruption():
    """Test that get_open_orders does NOT catch StateCorruptionError."""
    with patch("execution._load_active_trades", side_effect=StateCorruptionError("corrupt")):
        with pytest.raises(StateCorruptionError, match="corrupt"):
            execution.get_open_orders("BTCUSDT")

def test_validate_trade_schema_valid():
    trade = {
        "strategy": "scalper",
        "symbol": "BTCUSDT",
        "side": "BUY",
        "quantity": 0.001,
        "entry_price": 50000.0,
        "oco_id": 123,
        "tp_price": 51000.0,
        "sl_price": 49000.0,
        "state": execution.OrderState.PROTECTED,
        "signal_id": "test-uuid-001"
    , "entry_timestamp": "2026-08-15T12:00:00Z"}
    execution._validate_trade_schema(trade) # Should not raise

def test_validate_trade_schema_invalid():
    # Empty strategy
    trade = {
        "strategy": "",
        "symbol": "BTCUSDT",
        "side": "BUY",
        "quantity": 0.001,
        "entry_price": 50000.0,
        "oco_id": 123,
        "tp_price": 51000.0,
        "sl_price": 49000.0,
        "state": execution.OrderState.PROTECTED,
        "signal_id": "test-uuid-001"
    , "entry_timestamp": "2026-08-15T12:00:00Z"}
    with pytest.raises(StateCorruptionError, match="Invalid strategy"):
        execution._validate_trade_schema(trade)
        
    # Invalid symbol
    trade["strategy"] = "a"
    trade["symbol"] = 123
    with pytest.raises(StateCorruptionError, match="Invalid symbol"):
        execution._validate_trade_schema(trade)
        
    # Invalid quantity
    trade["symbol"] = "BTCUSDT"
    for bad_qty in [0, -1, "abc", math.nan, math.inf, -math.inf]:
        trade["quantity"] = bad_qty
        with pytest.raises(StateCorruptionError, match="positive finite number|Invalid quantity"):
            execution._validate_trade_schema(trade)
            
    # Invalid price
    trade["quantity"] = 1.0
    for bad_price in [0, -1, "abc", math.nan, math.inf, -math.inf]:
        trade["entry_price"] = bad_price
        with pytest.raises(StateCorruptionError, match="positive finite number|Invalid entry_price"):
            execution._validate_trade_schema(trade)
            
    trade["entry_price"] = 1000
    
    # Missing OCO ID but sl/tp exist
    trade["oco_id"] = None
    with pytest.raises(StateCorruptionError, match="oco_id cannot be None"):
        execution._validate_trade_schema(trade)

@patch("execution.TRADING_MODE", "TESTNET")
@patch("execution.TESTNET_ENABLED", True)
@patch("execution.PAPER_SAFE_MODE", False)
@patch("execution._save_active_trades")
@patch("execution._load_active_trades", return_value=[])
@patch("execution.log_trade")
@patch("execution.get_exchange_client")
def test_place_market_order_success(mock_get_client, mock_log, mock_load, mock_save):
    """Test that placing a market order and OCO order successfully returns the order."""
    
    mock_client = MagicMock()
    mock_get_client.return_value = mock_client
    
    # Mock Market Order
    mock_client.create_order.return_value = {
        "orderId": 123,
        "executedQty": "1.0",
        "cummulativeQuoteQty": "50000.0",
        "fills": [{"price": "50000.0", "commission": "0.5"}]
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
