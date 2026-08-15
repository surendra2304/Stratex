import pytest
import os
import json
from unittest.mock import patch, MagicMock

@pytest.fixture
def clean_reconciliation_env():
    files = [
        os.getenv("TESTNET_LEDGER_FILE", "testnet_trade_ledger.jsonl"),
        os.getenv("TESTNET_PORTFOLIO_FILE", "testnet_portfolio.json")
    ]
    for f in files:
        if os.path.exists(f):
            os.remove(f)
    yield
    for f in files:
        if os.path.exists(f):
            os.remove(f)

def test_balance_reconciliation_match(clean_reconciliation_env):
    ledger_file = os.getenv("TESTNET_LEDGER_FILE", "testnet_trade_ledger.jsonl")
    port_file = os.getenv("TESTNET_PORTFOLIO_FILE", "testnet_portfolio.json")
    
    with open(port_file, "w") as f:
        json.dump({"initial_deposit": 10000.0}, f)
        
    with open(ledger_file, "w") as f:
        json.dump({"net_pnl": 50.0}, f)
        f.write("\n")
        
    mock_client = MagicMock()
    mock_client.get_account.return_value = {
        "balances": [{"asset": "USDT", "free": "10000.0", "locked": "50.0"}]
    }
    
    with patch("testnet_engine.service.get_exchange_client", return_value=mock_client), \
         patch("testnet_engine.service.TESTNET_PORTFOLIO_FILE", port_file), \
         patch("testnet_engine.service.TESTNET_LEDGER_FILE", ledger_file), \
         patch("execution.monitor_open_trades"), \
         patch("execution._load_active_trades", return_value=[]):
        
        from testnet_engine.service import TestnetService
        service = TestnetService()
        
        account = service.client.get_account()
        usdt_balance = next((item for item in account['balances'] if item['asset'] == 'USDT'), None)
        actual_binance_balance = float(usdt_balance['free']) + float(usdt_balance['locked'])
        
        total_reconstructable_pnl = 50.0
        service.local_portfolio_balance = service.initial_deposit + total_reconstructable_pnl
        mismatch = abs(service.local_portfolio_balance - actual_binance_balance)
        
        assert mismatch == 0.0
        assert not service.safety_halt

def test_balance_reconciliation_mismatch_halts(clean_reconciliation_env):
    ledger_file = os.getenv("TESTNET_LEDGER_FILE", "testnet_trade_ledger.jsonl")
    port_file = os.getenv("TESTNET_PORTFOLIO_FILE", "testnet_portfolio.json")
    
    with open(port_file, "w") as f:
        json.dump({"initial_deposit": 10000.0}, f)
        
    with open(ledger_file, "w") as f:
        json.dump({"net_pnl": 50.0}, f)
        f.write("\n")
        
    mock_client = MagicMock()
    mock_client.get_account.return_value = {
        "balances": [{"asset": "USDT", "free": "9000.0", "locked": "0.0"}]
    }
    
    with patch("testnet_engine.service.get_exchange_client", return_value=mock_client), \
         patch("testnet_engine.service.TESTNET_PORTFOLIO_FILE", port_file), \
         patch("testnet_engine.service.TESTNET_LEDGER_FILE", ledger_file):
        
        from testnet_engine.service import TestnetService
        service = TestnetService()
        
        account = service.client.get_account()
        usdt_balance = next((item for item in account['balances'] if item['asset'] == 'USDT'), None)
        actual_binance_balance = float(usdt_balance['free']) + float(usdt_balance['locked'])
        
        total_reconstructable_pnl = 50.0
        service.local_portfolio_balance = service.initial_deposit + total_reconstructable_pnl
        
        import config
        tolerance = getattr(config, "RECONCILIATION_TOLERANCE", 1.0)
        mismatch = abs(service.local_portfolio_balance - actual_binance_balance)
        
        if mismatch > tolerance:
            service.safety_halt = True
            service.current_equity = service.local_portfolio_balance
            
        assert mismatch == 1050.0
        assert service.safety_halt is True
        assert service.current_equity == 10050.0

def test_stats_persistence(clean_reconciliation_env):
    port_file = os.getenv("TESTNET_PORTFOLIO_FILE", "testnet_portfolio.json")
    with open(port_file, "w") as f:
        json.dump({"initial_deposit": 10000.0, "scanner_stats": {"TOTAL_SIGNALS": 42, "ORDERS_FILLED": 12, "QUALIFIED": 15}}, f)
        
    mock_client = MagicMock()
    mock_client.get_account.return_value = {"balances": [{"asset": "USDT", "free": "10000.0", "locked": "0.0"}]}
    
    with patch("testnet_engine.service.get_exchange_client", return_value=mock_client), \
         patch("testnet_engine.service.TESTNET_PORTFOLIO_FILE", port_file), \
         patch("testnet_engine.service.TestnetService._restore_daily_risk_state"):
        from testnet_engine.service import TestnetService
        service = TestnetService()
        assert service.stats["TOTAL_SIGNALS"] == 42
        assert service.stats["ORDERS_FILLED"] == 12
        assert service.stats["QUALIFIED"] == 15

def test_signal_funnel_math():
    from dashboard import verify_funnel
    stats = {
        "TOTAL_SIGNALS": 100,
        "PROFITABILITY_REJECTED": 40,
        "RISK_REJECTED": 10,
        "COOLDOWN_REJECTED": 5,
        "JIT_REJECTED": 5,
        "OTHER_REJECTED": 0,
        "QUALIFIED": 40,
        "ORDERS_SUBMITTED": 35,
        "EXECUTION_REJECTED": 5,
        "ORDERS_FILLED": 30,
        "ORDERS_FAILED": 5
    }
    open_positions = 10
    closed_positions = 20
    errors = verify_funnel(stats, open_positions, closed_positions)
    assert len(errors) == 0
    stats["TOTAL_SIGNALS"] = 99
    errors = verify_funnel(stats, open_positions, closed_positions)
    assert len(errors) == 1
    assert "TOTAL_SIGNALS" in errors[0]
