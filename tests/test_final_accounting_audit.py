import os
import json
import pytest
import datetime
from unittest.mock import MagicMock, patch
from dashboard import app
from testnet_engine.service import TestnetService

@pytest.fixture
def client():
    app.config['TESTING'] = True
    with app.test_client() as client:
        yield client

def test_a_synthetic_test_record_excluded_from_dashboard_and_trade_count(client, tmp_path, monkeypatch):
    """Test A & D: Proves +$974 TEST record cannot appear in trade feed or change trade count."""
    ledger_file = tmp_path / "testnet_trade_ledger.jsonl"
    monkeypatch.setenv("TESTNET_LEDGER_FILE", str(ledger_file))
    monkeypatch.setattr("config.TRADING_MODE", "TESTNET")
    
    records = [
        {"timestamp": "2026-08-15T19:00:49Z", "symbol": "BTCUSDT", "strategy": "TEST", "source": "TEST", "action": "CLOSE_WIN", "quantity": 0.5, "entry_price": 50000.0, "exit_price": 52000.0, "pnl": 974.0, "net_pnl": 974.0, "entry_order_id": None, "exit_order_id": None},
        {"timestamp": "2026-08-14T11:00:35Z", "symbol": "BTCUSDT", "strategy": "RECOVERED", "source": "RECOVERY_FROM_BINANCE", "action": "CLOSED_LOSS", "quantity": 0.001, "entry_price": 63317.87, "exit_price": 63350.0, "pnl": -0.0321, "net_pnl": -0.0321, "entry_order_id": "2920255", "exit_order_id": "2920974"}
    ]
    with open(ledger_file, "w") as f:
        for r in records:
            f.write(json.dumps(r) + "\n")
            
    res = client.get("/api/trades")
    assert res.status_code == 200
    data = res.get_json()
    closed = [p for p in data.get("positions", []) if p.get("status") == "CLOSED"]
    
    assert len(closed) == 1
    assert closed[0]["pnl"] == -0.0321
    assert 974.0 not in [p["pnl"] for p in closed]

def test_b_c_synthetic_record_excluded_from_daily_risk_and_realized_pnl(tmp_path, monkeypatch):
    """Test B & C: Proves synthetic record cannot affect daily risk restoration or realized PnL."""
    ledger_file = tmp_path / "testnet_trade_ledger.jsonl"
    portfolio_file = tmp_path / "testnet_portfolio.json"
    
    monkeypatch.setenv("TESTNET_LEDGER_FILE", str(ledger_file))
    monkeypatch.setenv("TESTNET_PORTFOLIO_FILE", str(portfolio_file))
    monkeypatch.setattr("config.TRADING_MODE", "TESTNET")
    
    today_utc = datetime.datetime.utcnow().date().isoformat()
    records = [
        {"timestamp": f"{today_utc}T12:00:00Z", "symbol": "BTCUSDT", "strategy": "TEST", "source": "TEST", "action": "CLOSE_WIN", "quantity": 0.5, "entry_price": 50000.0, "exit_price": 52000.0, "pnl": 974.0, "net_pnl": 974.0, "entry_order_id": None, "exit_order_id": None},
        {"timestamp": f"{today_utc}T13:00:00Z", "symbol": "BTCUSDT", "strategy": "ADX_EMA", "source": "BINANCE_EXECUTION", "action": "CLOSE_WIN", "quantity": 0.001, "entry_price": 63000.0, "exit_price": 63100.0, "pnl": 0.10, "net_pnl": 0.10, "entry_order_id": "9991", "exit_order_id": "9992"}
    ]
    with open(ledger_file, "w") as f:
        for r in records:
            f.write(json.dumps(r) + "\n")
            
    mock_client = MagicMock()
    mock_client.get_account.return_value = {'balances': [{'asset': 'USDT', 'free': '10000.0', 'locked': '0.0'}]}
    mock_client.get_open_orders.return_value = []
    
    with patch("testnet_engine.service.get_exchange_client", return_value=mock_client), \
         patch("execution.get_exchange_client", return_value=mock_client):
        service = TestnetService()
        assert service.risk_gate.daily_realized_loss == 0.10
        assert service.risk_gate.daily_realized_loss != 974.10

def test_e_f_multi_asset_unrealized_pnl_calculation(client, tmp_path, monkeypatch):
    """Test E & F: Proves BTC and ETH positions calculate unrealized PnL independently with correct prices."""
    portfolio_file = tmp_path / "testnet_portfolio.json"
    monkeypatch.setenv("TESTNET_PORTFOLIO_FILE", str(portfolio_file))
    monkeypatch.setattr("config.TRADING_MODE", "TESTNET")
    
    port_state = {
        "initial_deposit": 10000.0,
        "cash": 10000.0,
        "equity": 10000.0,
        "realized_pnl": 0.0,
        "service_start_time": "2026-08-15T10:00:00Z",
        "positions": {
            "BTCUSDT": {"symbol": "BTCUSDT", "direction": "BUY", "quantity": 0.001, "entry_price": 60000.0, "status": "OPEN"},
            "ETHUSDT": {"symbol": "ETHUSDT", "direction": "BUY", "quantity": 0.01, "entry_price": 3000.0, "status": "OPEN"}
        }
    }
    with open(portfolio_file, "w") as f:
        json.dump(port_state, f)
        
    mock_client = MagicMock()
    mock_client.get_account.return_value = {'balances': [{'asset': 'USDT', 'free': '10000.0', 'locked': '0.0'}]}
    
    import pandas as pd
    def mock_fetch_candles(symbol, timeframe, limit):
        if symbol == "BTCUSDT":
            return pd.DataFrame({"close": [62000.0]})  # +2000 * 0.001 = +$2.00
        elif symbol == "ETHUSDT":
            return pd.DataFrame({"close": [3100.0]})   # +100 * 0.01 = +$1.00
        return pd.DataFrame({"close": [0.0]})
        
    with patch("execution.get_exchange_client", return_value=mock_client), \
         patch("dashboard.fetch_candles", side_effect=mock_fetch_candles):
        res = client.get("/api/status")
        assert res.status_code == 200
        data = res.get_json()
        # Unrealized PnL should be exactly 2.00 + 1.00 = 3.00
        assert pytest.approx(data["unrealized_pnl"], 0.001) == 3.00
        assert pytest.approx(data["equity"], 0.001) == 10003.00

def test_g_malformed_position_does_not_break_status(client, tmp_path, monkeypatch):
    """Test G: Proves one malformed position does not destroy the entire /api/status response."""
    portfolio_file = tmp_path / "testnet_portfolio.json"
    monkeypatch.setenv("TESTNET_PORTFOLIO_FILE", str(portfolio_file))
    monkeypatch.setattr("config.TRADING_MODE", "TESTNET")
    
    port_state = {
        "initial_deposit": 10000.0,
        "cash": 10000.0,
        "equity": 10000.0,
        "realized_pnl": 5.0,
        "positions": {
            "BAD_POS": "not a dict",
            "MALFORMED": {"status": "OPEN", "quantity": "invalid_number"},
            "GOOD_POS": {"symbol": "BTCUSDT", "direction": "BUY", "quantity": 0.001, "entry_price": 60000.0, "status": "OPEN"}
        }
    }
    with open(portfolio_file, "w") as f:
        json.dump(port_state, f)
        
    mock_client = MagicMock()
    mock_client.get_account.return_value = {'balances': [{'asset': 'USDT', 'free': '10000.0', 'locked': '0.0'}]}
    
    import pandas as pd
    with patch("execution.get_exchange_client", return_value=mock_client), \
         patch("dashboard.fetch_candles", return_value=pd.DataFrame({"close": [61000.0]})):
        res = client.get("/api/status")
        assert res.status_code == 200
        data = res.get_json()
        assert data["realized_pnl"] == 5.0
        assert data["open_positions"] == 2
        # Good position calculated: (61000 - 60000) * 0.001 = 1.00
        assert pytest.approx(data["unrealized_pnl"], 0.001) == 1.00

def test_h_i_j_daily_equity_high_low_utc_filtering(client, tmp_path, monkeypatch):
    """Test H, I, J: Proves Daily High/Low and Change only use today's UTC equity snapshots."""
    portfolio_file = tmp_path / "testnet_portfolio.json"
    hist_file = tmp_path / "testnet_equity_history.jsonl"
    
    monkeypatch.setenv("TESTNET_PORTFOLIO_FILE", str(portfolio_file))
    monkeypatch.setenv("TESTNET_EQUITY_HISTORY_FILE", str(hist_file))
    monkeypatch.setattr("config.TRADING_MODE", "TESTNET")
    
    today_utc = datetime.datetime.utcnow().date().isoformat()
    yesterday_utc = (datetime.datetime.utcnow().date() - datetime.timedelta(days=1)).isoformat()
    
    # Yesterday's history had extreme values 5,000 and 20,000 (must NOT be used for today's High/Low)
    snaps = [
        {"timestamp": f"{yesterday_utc}T10:00:00Z", "equity": 5000.0, "balance": 5000.0},
        {"timestamp": f"{yesterday_utc}T20:00:00Z", "equity": 20000.0, "balance": 20000.0},
        {"timestamp": f"{today_utc}T01:00:00Z", "equity": 10000.0, "balance": 10000.0},
        {"timestamp": f"{today_utc}T05:00:00Z", "equity": 10500.0, "balance": 10500.0},
        {"timestamp": f"{today_utc}T10:00:00Z", "equity": 9800.0, "balance": 9800.0},
        {"timestamp": f"{today_utc}T15:00:00Z", "equity": 10200.0, "balance": 10200.0}
    ]
    with open(hist_file, "w") as f:
        for s in snaps:
            f.write(json.dumps(s) + "\n")
            
    mock_client = MagicMock()
    mock_client.get_account.return_value = {'balances': [{'asset': 'USDT', 'free': '10000.0', 'locked': '0.0'}]}
    
    with patch("testnet_engine.service.get_exchange_client", return_value=mock_client), \
         patch("execution.get_exchange_client", return_value=mock_client):
        res = client.get("/api/status")
        assert res.status_code == 200
        data = res.get_json()
        
        # Today's snapshots: [10000.0, 10500.0, 9800.0, 10200.0]
        # Daily High must be 10500.0 (NOT 20000.0 from yesterday)
        assert data["equity_high"] == 10500.0
        # Daily Low must be 9800.0 (NOT 5000.0 from yesterday)
        assert data["equity_low"] == 9800.0
        # Daily Change = (10200 - 10000) / 10000 * 100 = +2.0%
        assert pytest.approx(data["equity_change"], 0.01) == 2.0

def test_k_l_no_duplicate_recovery_accounting(tmp_path, monkeypatch):
    """Test K & L: Proves duplicate Binance fills / recovery records are not double counted."""
    ledger_file = tmp_path / "testnet_trade_ledger.jsonl"
    monkeypatch.setenv("TESTNET_LEDGER_FILE", str(ledger_file))
    monkeypatch.setattr("config.TRADING_MODE", "TESTNET")
    
    mock_client = MagicMock()
    mock_client.get_account.return_value = {'balances': [{'asset': 'USDT', 'free': '10000.0', 'locked': '0.0'}, {'asset': 'BTC', 'free': '0.0', 'locked': '0.0'}]}
    mock_client.get_open_orders.return_value = []
    
    filled_orders = [
        {"orderId": 2001, "symbol": "BTCUSDT", "side": "BUY", "executedQty": "0.001", "cummulativeQuoteQty": "60.0", "status": "FILLED", "time": 1000},
        {"orderId": 2002, "symbol": "BTCUSDT", "side": "SELL", "executedQty": "0.001", "cummulativeQuoteQty": "61.0", "status": "FILLED", "time": 2000}
    ]
    mock_client.get_all_orders.return_value = filled_orders
    mock_client.get_my_trades.return_value = [
        {"id": 101, "orderId": 2001, "symbol": "BTCUSDT", "commission": "0.0"},
        {"id": 102, "orderId": 2002, "symbol": "BTCUSDT", "commission": "0.0"}
    ]
    
    with patch("testnet_engine.service.get_exchange_client", return_value=mock_client), \
         patch("execution.get_exchange_client", return_value=mock_client):
        service = TestnetService()
        # Perform rebuild twice
        service._rebuild_testnet_state()
        service._rebuild_testnet_state()
        
        # Verify ledger has exactly 1 trade (no duplicate appended)
        with open(ledger_file, "r") as f:
            lines = [l for l in f.readlines() if l.strip()]
        assert len(lines) == 1
        record = json.loads(lines[0])
        assert record["source"] == "RECOVERY_FROM_BINANCE"
        assert record["net_pnl"] == 1.0

def test_m_dashboard_pnl_and_authoritative_accounting_reconcile(client, tmp_path, monkeypatch):
    """Test M: Proves trade feed and status PnL reconcile accurately."""
    portfolio_file = tmp_path / "testnet_portfolio.json"
    ledger_file = tmp_path / "testnet_trade_ledger.jsonl"
    
    monkeypatch.setenv("TESTNET_PORTFOLIO_FILE", str(portfolio_file))
    monkeypatch.setenv("TESTNET_LEDGER_FILE", str(ledger_file))
    monkeypatch.setattr("config.TRADING_MODE", "TESTNET")
    
    port_state = {
        "initial_deposit": 10000.0,
        "cash": 10001.50,
        "equity": 10001.50,
        "realized_pnl": 1.50,
        "positions": {}
    }
    with open(portfolio_file, "w") as f:
        json.dump(port_state, f)
        
    records = [
        {"timestamp": "2026-08-14T11:00:00Z", "symbol": "BTCUSDT", "strategy": "ADX_EMA", "source": "BINANCE_EXECUTION", "action": "CLOSE_WIN", "quantity": 0.001, "entry_price": 60000.0, "exit_price": 61500.0, "pnl": 1.50, "net_pnl": 1.50, "entry_order_id": "111", "exit_order_id": "222"}
    ]
    with open(ledger_file, "w") as f:
        for r in records:
            f.write(json.dumps(r) + "\n")
            
    mock_client = MagicMock()
    mock_client.get_account.return_value = {'balances': [{'asset': 'USDT', 'free': '10001.50', 'locked': '0.0'}]}
    
    with patch("execution.get_exchange_client", return_value=mock_client):
        res_status = client.get("/api/status").get_json()
        res_trades = client.get("/api/trades").get_json()
        
        sum_closed_pnl = sum([p["pnl"] for p in res_trades["positions"] if p["status"] == "CLOSED"])
        assert res_status["realized_pnl"] == sum_closed_pnl == 1.50
        assert res_trades["net_pnl"] == 1.50
        assert res_trades["total_trades"] == 1

def test_duplicate_exit_order_id_deduplication(client, tmp_path, monkeypatch):
    """Proves duplicate identical exit_order_id in ledger is counted only once in /api/trades and risk restore."""
    portfolio_file = tmp_path / "testnet_portfolio.json"
    ledger_file = tmp_path / "testnet_trade_ledger.jsonl"
    
    monkeypatch.setenv("TESTNET_PORTFOLIO_FILE", str(portfolio_file))
    monkeypatch.setenv("TESTNET_LEDGER_FILE", str(ledger_file))
    monkeypatch.setattr("config.TRADING_MODE", "TESTNET")
    
    today_utc = datetime.datetime.utcnow().date().isoformat()
    records = [
        {"timestamp": f"{today_utc}T10:00:00Z", "symbol": "BTCUSDT", "strategy": "ADX_EMA", "source": "BINANCE_EXECUTION", "action": "CLOSE_WIN", "quantity": 0.001, "entry_price": 60000.0, "exit_price": 61000.0, "pnl": 1.00, "net_pnl": 1.00, "entry_order_id": "1001", "exit_order_id": "2001"},
        {"timestamp": f"{today_utc}T10:00:01Z", "symbol": "BTCUSDT", "strategy": "ADX_EMA", "source": "BINANCE_EXECUTION", "action": "CLOSE_WIN", "quantity": 0.001, "entry_price": 60000.0, "exit_price": 61000.0, "pnl": 1.00, "net_pnl": 1.00, "entry_order_id": "1001", "exit_order_id": "2001"}
    ]
    with open(ledger_file, "w") as f:
        for r in records:
            f.write(json.dumps(r) + "\n")
            
    res_trades = client.get("/api/trades").get_json()
    assert res_trades["total_trades"] == 1
    assert res_trades["net_pnl"] == 1.00
    assert len(res_trades["positions"]) == 1
    
    mock_client = MagicMock()
    mock_client.get_account.return_value = {'balances': [{'asset': 'USDT', 'free': '10000.0', 'locked': '0.0'}]}
    mock_client.get_open_orders.return_value = []
    with patch("testnet_engine.service.get_exchange_client", return_value=mock_client), \
         patch("execution.get_exchange_client", return_value=mock_client):
        service = TestnetService()
        assert service.risk_gate.daily_realized_loss == 1.00

def test_original_plus_recovery_representation_deduplication(client, tmp_path, monkeypatch):
    """Proves original Binance execution and recovery record of same trade (same exit_order_id) are not double counted."""
    portfolio_file = tmp_path / "testnet_portfolio.json"
    ledger_file = tmp_path / "testnet_trade_ledger.jsonl"
    
    monkeypatch.setenv("TESTNET_PORTFOLIO_FILE", str(portfolio_file))
    monkeypatch.setenv("TESTNET_LEDGER_FILE", str(ledger_file))
    monkeypatch.setattr("config.TRADING_MODE", "TESTNET")
    
    today_utc = datetime.datetime.utcnow().date().isoformat()
    records = [
        {"timestamp": f"{today_utc}T09:00:00Z", "symbol": "BTCUSDT", "strategy": "ADX_EMA", "source": "BINANCE_EXECUTION", "action": "CLOSE_WIN", "quantity": 0.001, "entry_price": 60000.0, "exit_price": 61500.0, "pnl": 1.50, "net_pnl": 1.50, "entry_order_id": "3001", "exit_order_id": "4001"},
        {"timestamp": f"{today_utc}T09:00:00Z", "symbol": "BTCUSDT", "strategy": "RECOVERED", "source": "RECOVERY_FROM_BINANCE", "action": "CLOSE_WIN", "quantity": 0.001, "entry_price": 60000.0, "exit_price": 61500.0, "pnl": 1.50, "net_pnl": 1.50, "entry_order_id": "3001", "exit_order_id": "4001"}
    ]
    with open(ledger_file, "w") as f:
        for r in records:
            f.write(json.dumps(r) + "\n")
            
    res_trades = client.get("/api/trades").get_json()
    assert res_trades["total_trades"] == 1
    assert res_trades["net_pnl"] == 1.50
    assert len(res_trades["positions"]) == 1

def test_synthetic_test_record_zero_influence_on_all_metrics(client, tmp_path, monkeypatch):
    """Proves TEST +$974 record cannot change net_pnl, total_trades, wins/losses, or profit factor."""
    portfolio_file = tmp_path / "testnet_portfolio.json"
    ledger_file = tmp_path / "testnet_trade_ledger.jsonl"
    
    monkeypatch.setenv("TESTNET_PORTFOLIO_FILE", str(portfolio_file))
    monkeypatch.setenv("TESTNET_LEDGER_FILE", str(ledger_file))
    monkeypatch.setattr("config.TRADING_MODE", "TESTNET")
    
    records = [
        {"timestamp": "2026-08-15T19:00:00Z", "symbol": "BTCUSDT", "strategy": "TEST", "source": "TEST", "action": "CLOSE_WIN", "quantity": 0.5, "entry_price": 50000.0, "exit_price": 52000.0, "pnl": 974.0, "net_pnl": 974.0, "entry_order_id": None, "exit_order_id": None},
        {"timestamp": "2026-08-14T11:00:00Z", "symbol": "BTCUSDT", "strategy": "ADX_EMA", "source": "BINANCE_EXECUTION", "action": "CLOSE_WIN", "quantity": 0.001, "entry_price": 60000.0, "exit_price": 62000.0, "pnl": 2.00, "net_pnl": 2.00, "entry_order_id": "7001", "exit_order_id": "7002"}
    ]
    with open(ledger_file, "w") as f:
        for r in records:
            f.write(json.dumps(r) + "\n")
            
    res_trades = client.get("/api/trades").get_json()
    assert res_trades["net_pnl"] == 2.00
    assert res_trades["total_trades"] == 1
    assert res_trades["win_rate"] == 100.0
    assert len(res_trades["positions"]) == 1
    assert res_trades["positions"][0]["pnl"] == 2.00

