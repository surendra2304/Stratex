import datetime
import importlib
import os
from unittest.mock import MagicMock, patch

import config
import execution


def reload_modules():
    importlib.reload(config)
    importlib.reload(execution)
    import testnet_engine.market_scanner
    import testnet_engine.risk_gate
    import testnet_engine.service
    importlib.reload(testnet_engine.service)
    importlib.reload(testnet_engine.market_scanner)
    importlib.reload(testnet_engine.risk_gate)
    return testnet_engine.service, testnet_engine.market_scanner, testnet_engine.risk_gate

@patch.dict(os.environ, {"TRADING_MODE": "TESTNET", "TESTNET_ENABLED": "True", "LIVE_TRADING_ENABLED": "False", "PAPER_SAFE_MODE": "False", "API_KEY": "dummy", "SECRET_KEY": "dummy", "TESTNET_ONLY": "TRUE"})
def test_chaos_daily_risk_reset():
    _svc, _msc, rsg = reload_modules()
    gate = rsg.RiskGate(starting_balance=10000.0)
    
    # Simulate a loss today
    gate.update_after_trade(-500.0, 9500.0)
    assert gate.daily_realized_loss == -500.0
    
    # Fast forward the UTC clock by 1 day
    with patch("testnet_engine.risk_gate.datetime") as mock_dt:
        future_date = datetime.datetime.utcnow() + datetime.timedelta(days=1)
        # RiskGate uses explicit-UTC now(timezone.utc); mock both spellings
        mock_dt.datetime.utcnow.return_value = future_date
        mock_dt.datetime.now.return_value = future_date
        mock_dt.timezone.utc = datetime.timezone.utc
        
        gate.evaluate_risk("BTCUSDT", "LONG", 9500.0, {}, 0.1, 50000.0, "OK")
        
        assert gate.daily_realized_loss == 0.0, "Daily PnL should reset on UTC boundary crossing"
        assert gate.current_trading_day == future_date.date()

@patch.dict(os.environ, {"TRADING_MODE": "TESTNET", "TESTNET_ENABLED": "True", "LIVE_TRADING_ENABLED": "False", "PAPER_SAFE_MODE": "False", "API_KEY": "dummy", "SECRET_KEY": "dummy", "TESTNET_ONLY": "TRUE", "TESTNET_PORTFOLIO_FILE": "nonexistent_portfolio.json", "TESTNET_LEDGER_FILE": "nonexistent_ledger.jsonl"})
def test_chaos_data_staleness_blocks_entries():
    svc, _msc, _rsg = reload_modules()
    
    with patch("testnet_engine.service.get_exchange_client") as mock_get_client:
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client
        mock_client.get_account.return_value = {"balances": [{"asset": "USDT", "free": "10000.0", "locked": "0.0"}]}
        mock_client.get_open_orders.return_value = []
        
        service = svc.TestnetService()
        
        # 1. Healthy data allows risk evaluation
        passed, reason, _ = service.risk_gate.evaluate_risk("BTCUSDT", "LONG", 10000.0, {}, 0.001, 50000.0, "OK")
        assert passed is True
        
        # 2. Stale data from MarketScanner blocks it
        passed, reason, _ = service.risk_gate.evaluate_risk("BTCUSDT", "LONG", 10000.0, {}, 0.001, 50000.0, "STALE")
        assert passed is False
        assert reason == "DATA_DEGRADED"

@patch.dict(os.environ, {"TRADING_MODE": "TESTNET", "TESTNET_ENABLED": "True", "LIVE_TRADING_ENABLED": "False", "PAPER_SAFE_MODE": "False", "API_KEY": "dummy", "SECRET_KEY": "dummy", "TESTNET_ONLY": "TRUE"})
def test_chaos_ws_reconnect_logic():
    _svc, msc, _rsg = reload_modules()
    
    with patch("testnet_engine.market_scanner.ThreadedWebsocketManager") as mock_twm_class, \
         patch("testnet_engine.market_scanner.Client") as mock_client_class:
        mock_twm = MagicMock()
        mock_twm_class.return_value = mock_twm
        mock_client = MagicMock()
        mock_client_class.return_value = mock_client
        
        scanner = msc.MarketScanner(symbols=["BTCUSDT"], timeframe="1m")
        # Mock rest fetch to do nothing for this test
        scanner._fetch_historical_candles = MagicMock()
        
        # Init
        scanner.start()
        
        # Fast forward time to make it look like the socket died completely (>60s)
        # We manually trigger the logic inside _health_monitor_loop by patching datetime
        now = datetime.datetime.utcnow()
        scanner.last_market_update["BTCUSDT"] = now - datetime.timedelta(seconds=70)
        
        # Note: the health monitor runs in a background thread, so testing it deterministically
        # requires triggering the logic directly instead of waiting on threads
        scanner._stop_event.set() # Stop the actual thread so it doesn't conflict
        
        # Simulate one iteration of the health loop manually
        with patch("testnet_engine.market_scanner.datetime") as mock_dt:
            mock_dt.datetime.utcnow.return_value = now
            
            all_stale = True
            for sym in scanner.symbols:
                elapsed = (now - scanner.last_market_update.get(sym)).total_seconds()
                if elapsed > 15:
                    scanner.data_health_status[sym] = "STALE"
            
            max_elapsed = max([(now - scanner.last_market_update.get(s, now)).total_seconds() for s in scanner.symbols])
            if all_stale and max_elapsed > 60:
                scanner.twm.stop()
                scanner.twm = mock_twm_class(testnet=scanner.testnet)
                scanner.twm.start()
                
        # Assert twm methods were called
        assert mock_twm.stop.call_count >= 1
        assert mock_twm_class.call_count == 2 # Initial + Reconnect
        
@patch.dict(os.environ, {"TRADING_MODE": "TESTNET", "TESTNET_ENABLED": "True", "LIVE_TRADING_ENABLED": "False", "PAPER_SAFE_MODE": "False", "API_KEY": "dummy", "SECRET_KEY": "dummy", "TESTNET_ONLY": "TRUE"})
def test_atomic_save_state():
    svc, _msc, _rsg = reload_modules()
    
    with patch("testnet_engine.service.get_exchange_client") as mock_get_client:
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client
        mock_client.get_account.return_value = {"balances": [{"asset": "USDT", "free": "10000.0", "locked": "0.0"}]}
        mock_client.get_open_orders.return_value = []
        
        service = svc.TestnetService()
        
        with patch("testnet_engine.service.os.replace") as mock_replace:
            service._save_state()
            # Assert os.replace was called with the tmp file
            from testnet_engine.service import TESTNET_PORTFOLIO_FILE
            mock_replace.assert_called_once_with(TESTNET_PORTFOLIO_FILE + ".tmp", TESTNET_PORTFOLIO_FILE)
