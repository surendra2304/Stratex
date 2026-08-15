import pytest
import os
import json
import datetime
from unittest.mock import patch, MagicMock

from execution import monitor_open_trades, _save_active_trades, _load_active_trades
from testnet_engine.service import TestnetService
from testnet_engine.protection import emergency_market_close

@pytest.fixture
def clean_env():
    """Ensure clean state files before each test."""
    files = [
        os.getenv("ACTIVE_TRADES_FILE", "active_trades.json"), 
        os.getenv("TESTNET_LEDGER_FILE", "testnet_trade_ledger.jsonl")
    ]
    for f in files:
        if os.path.exists(f):
            os.remove(f)
    yield
    for f in files:
        if os.path.exists(f):
            os.remove(f)

@pytest.fixture
def mock_client():
    client = MagicMock()
    return client

def test_tp_fill(clean_env, mock_client):
    # Setup local active trade
    t = {
        "strategy": "TEST",
        "symbol": "BTCUSDT",
        "side": "BUY",
        "quantity": 0.5,
        "entry_price": 50000.0,
        "sl_price": 49000.0,
        "tp_price": 52000.0,
        "oco_id": 999,
        "state": 1,
        "signal_id": "test_mock_123"
    , "entry_timestamp": "2026-08-15T12:00:00Z"}
    _save_active_trades([t])

    # Mock get_exchange_client
    with patch("execution.get_exchange_client", return_value=mock_client), \
         patch("testnet_engine.protection.check_oco_status") as mock_status:
        
        mock_status.return_value = {
            "list_status": "ALL_DONE",
            "close_avg_price": 52000.0,
            "close_qty": 0.5,
            "tp_filled": True,
            "sl_filled": False
        }
        
        monitor_open_trades()
        
        # Verify ledger
        ledger_file = os.getenv("TESTNET_LEDGER_FILE", "testnet_trade_ledger.jsonl")
        assert os.path.exists(ledger_file)
        with open(ledger_file, "r") as f:
            lines = f.readlines()
            assert len(lines) == 1
            ledger = json.loads(lines[0])
            assert ledger["action"] == "CLOSE_WIN"
            assert ledger["net_pnl"] > 0
            
        # Verify active_trades is empty
        active = _load_active_trades()
        assert len(active) == 0

def test_sl_fill(clean_env, mock_client):
    t = {
        "strategy": "TEST",
        "symbol": "BTCUSDT",
        "side": "BUY",
        "quantity": 0.5,
        "entry_price": 50000.0,
        "sl_price": 49000.0,
        "tp_price": 52000.0,
        "oco_id": 999,
        "state": 1,
        "signal_id": "test_mock_123"
    , "entry_timestamp": "2026-08-15T12:00:00Z"}
    _save_active_trades([t])

    with patch("execution.get_exchange_client", return_value=mock_client), \
         patch("testnet_engine.protection.check_oco_status") as mock_status:
        
        mock_status.return_value = {
            "list_status": "ALL_DONE",
            "close_avg_price": 49000.0,
            "close_qty": 0.5,
            "tp_filled": False,
            "sl_filled": True
        }
        
        monitor_open_trades()
        
        ledger_file = os.getenv("TESTNET_LEDGER_FILE", "testnet_trade_ledger.jsonl")
        with open(ledger_file, "r") as f:
            ledger = json.loads(f.readlines()[0])
            assert ledger["action"] == "CLOSE_LOSS"
            assert ledger["net_pnl"] < 0

def test_orphaned_local_position(clean_env, mock_client):
    # Local state has a trade, but Binance says it doesn't exist
    t = {
        "strategy": "TEST",
        "symbol": "BTCUSDT",
        "side": "BUY",
        "quantity": 0.5,
        "entry_price": 50000.0,
        "sl_price": 49000.0,
        "tp_price": 52000.0,
        "oco_id": 999,
        "state": 1,
        "signal_id": "test_mock_123"
    , "entry_timestamp": "2026-08-15T12:00:00Z"}
    _save_active_trades([t])

    with patch("execution.get_exchange_client", return_value=mock_client):
        from binance.exceptions import BinanceAPIException
        mock_response = MagicMock()
        mock_response.status_code = 400
        # Exception signature: response, status_code, text
        mock_client.v3_get_order_list.side_effect = BinanceAPIException(mock_response, 400, '{"msg": "Order does not exist"}')
        # mock balance to be 0
        mock_client.get_asset_balance.return_value = {"free": "0.0", "locked": "0.0"}
        
        monitor_open_trades()
        
        # Because balance is 0, it purges silently without emergency close
        assert not mock_client.create_order.called
        active = _load_active_trades()
        assert len(active) == 0

def test_reconstruct_active_trades(clean_env, mock_client):
    # Test restart recovery
    mock_client.get_open_orders.return_value = [
        {"symbol": "BTCUSDT", "orderListId": 999, "type": "LIMIT_MAKER", "orderId": 100, "price": "52000"},
        {"symbol": "BTCUSDT", "orderListId": 999, "type": "STOP_LOSS_LIMIT", "orderId": 101, "stopPrice": "49000"}
    ]
    mock_client.get_my_trades.return_value = [
        {"orderId": 50, "price": "50000", "qty": "0.5", "isBuyer": True}
    ]
    account = {
        "balances": [
            {"asset": "USDT", "free": "1000", "locked": "0"},
            {"asset": "BTC", "free": "0.5", "locked": "0.0"}
        ]
    }
    mock_client.get_account.return_value = account
    
    with patch("testnet_engine.service.get_exchange_client", return_value=mock_client):
        service = TestnetService()
        service.client = mock_client
        service.sync_exchange_state(account)
        
        active = _load_active_trades()
        assert len(active) == 1
        assert active[0]["symbol"] == "BTCUSDT"
        assert active[0]["oco_id"] == 999
        assert active[0]["entry_price"] == 50000.0
        assert active[0]["side"] == "BUY"

def test_cancelled_protection(clean_env, mock_client):
    t = {
        "strategy": "TEST",
        "symbol": "BTCUSDT",
        "side": "BUY",
        "quantity": 0.5,
        "entry_price": 50000.0,
        "sl_price": 49000.0,
        "tp_price": 52000.0,
        "oco_id": 999,
        "state": 1,
        "signal_id": "test_mock_123"
    , "entry_timestamp": "2026-08-15T12:00:00Z"}
    _save_active_trades([t])

    with patch("execution.get_exchange_client", return_value=mock_client), \
         patch("testnet_engine.protection.check_oco_status") as mock_status, \
         patch("testnet_engine.protection.emergency_market_close") as mock_ec:
        
        mock_status.return_value = {
            "list_status": "CANCELED"
        }
        mock_ec.return_value = {"executedQty": "0.5"}
        
        monitor_open_trades()
        
        assert mock_ec.called
        
        ledger_file = os.getenv("TESTNET_LEDGER_FILE", "testnet_trade_ledger.jsonl")
        with open(ledger_file, "r") as f:
            ledger = json.loads(f.readlines()[0])
            assert ledger["action"] == "EMERGENCY_CLOSE"
            assert ledger["quantity"] == 0.5
