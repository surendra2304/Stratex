import importlib
import os
import uuid
from unittest.mock import MagicMock, patch

import config
import execution


def reload_modules():
    importlib.reload(config)
    importlib.reload(execution)
    import testnet_engine.service
    importlib.reload(testnet_engine.service)
    return testnet_engine.service.TestnetService

@patch.dict(os.environ, {"TRADING_MODE": "TESTNET", "TESTNET_ENABLED": "True", "LIVE_TRADING_ENABLED": "False", "PAPER_SAFE_MODE": "False", "API_KEY": "dummy", "SECRET_KEY": "dummy"})
def test_duplicate_client_order_id_protection():
    reload_modules()
    
    # 1. Simulate saving an active trade with a specific client ID
    client_id = "test-uuid-123"
    trade_data = [{
        "strategy": "ml",
        "symbol": "BTCUSDT",
        "side": "BUY",
        "quantity": 1.0,
        "entry_price": 50000.0,
        "oco_id": 999,
        "tp_price": 55000.0,
        "sl_price": 48000.0,
        "state": execution.OrderState.PROTECTED,
        "signal_id": client_id
    , "entry_timestamp": "2026-08-15T12:00:00Z"}]
    
    # Pre-populate active trades
    execution._save_active_trades(trade_data)
    
    # 2. Attempt to place another order with the EXACT same client_id
    # execution.place_market_order should reject it and return None
    with patch("execution.get_exchange_client") as mock_get_client:
        order = execution.place_market_order("ml", "BUY", "BTCUSDT", quantity=1.0, client_order_id=client_id)
        assert order is None, "Order with duplicate client ID should be rejected before submission"
        mock_get_client.assert_not_called()

@patch.dict(os.environ, {"TRADING_MODE": "TESTNET", "TESTNET_ENABLED": "True", "LIVE_TRADING_ENABLED": "False", "PAPER_SAFE_MODE": "False", "API_KEY": "dummy", "SECRET_KEY": "dummy"})
def test_partial_fill_and_pnl_accounting():
    reload_modules()
    
    # Clear state
    if os.path.exists(execution.ACTIVE_TRADES_FILE):
        os.remove(execution.ACTIVE_TRADES_FILE)
        
    client_id = str(uuid.uuid4())
    
    with patch("execution.get_exchange_client") as mock_get_client:
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client
        
        # Simulate partial fill from Binance
        mock_client.create_order.return_value = {
            "orderId": 100,
            "origQty": "1.0",
            "executedQty": "0.5",
            "cummulativeQuoteQty": "25000.0",
            "fills": [
                {"price": "50000.0", "qty": "0.5", "commission": "1.5"}
            ]
        }
        
        mock_client.create_oco_order.return_value = {
            "orderListId": 200
        }
        
        order_res = execution.place_market_order("ml", "BUY", "BTCUSDT", quantity=1.0, sl=48000, tp=55000, client_order_id=client_id)
        
        assert order_res is not None
        assert order_res["_actual_price"] == 50000.0
        assert order_res["_executed_qty"] == 0.5
        assert order_res["_total_fee"] == 1.5
        
        # Verify state file was saved with the PARTIALLY_FILLED properties
        active = execution._load_active_trades()
        assert len(active) == 1
        assert active[0]["state"] == execution.OrderState.PROTECTED
        assert active[0]["quantity"] == 0.5
        assert active[0]["entry_price"] == 50000.0

@patch.dict(os.environ, {"TRADING_MODE": "TESTNET", "TESTNET_ENABLED": "True", "LIVE_TRADING_ENABLED": "False", "PAPER_SAFE_MODE": "False", "API_KEY": "dummy", "SECRET_KEY": "dummy", "TESTNET_ONLY": "TRUE"})
def test_restart_recovery_missing_protection_safety_halt():
    TestnetService_dynamic = reload_modules()
    
    # Mock the client directly for TestnetService instantiation
    with patch("testnet_engine.service.get_exchange_client") as mock_get_client:
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client
        
        # Binance says we have 1 BTC!
        mock_client.get_account.return_value = {
            "balances": [
                {"asset": "USDT", "free": "10000.0", "locked": "0.0"},
                {"asset": "BTC", "free": "1.0", "locked": "0.0"}
            ]
        }
        
        # Binance says we have NO open OCO orders! (Unprotected)
        mock_client.get_open_orders.return_value = []
        
        service = TestnetService_dynamic()
        
        # Because we hold 1 BTC but have no open orders, it must trigger SAFETY_HALT
        assert service.safety_halt is True, "Service should halt on missing protection"
        
@patch.dict(os.environ, {"TRADING_MODE": "TESTNET", "TESTNET_ENABLED": "True", "LIVE_TRADING_ENABLED": "False", "PAPER_SAFE_MODE": "False", "API_KEY": "dummy", "SECRET_KEY": "dummy", "TESTNET_ONLY": "TRUE"})
def test_restart_recovery_matched_state():
    TestnetService_dynamic = reload_modules()
    
    with patch("testnet_engine.service.get_exchange_client") as mock_get_client:
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client
        
        # Binance says we have 1 BTC
        mock_client.get_account.return_value = {
            "balances": [
                {"asset": "USDT", "free": "10000.0", "locked": "0.0"},
                {"asset": "BTC", "free": "1.0", "locked": "0.0"}
            ]
        }
        
        # Binance says we DO have an open order for BTCUSDT (so we are protected)
        mock_client.get_open_orders.return_value = [
            {"symbol": "BTCUSDT", "orderId": 201}
        ]
        
        service = TestnetService_dynamic()
        
        # Should not halt
        assert service.safety_halt is False, "Service should NOT halt when positions are correctly protected"
