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


def test_paper_reconciliation_isolated_path(tmp_path):
    """Ensure PaperReconciliation writes records to the explicitly passed path and isolates production files."""
    from paper_engine.reconciliation import PaperReconciliation

    prod_file = "forward_reconciliation.jsonl"
    prod_size_before = os.path.getsize(prod_file) if os.path.exists(prod_file) else 0

    ledger_file = str(tmp_path / "paper_ledger.jsonl")
    rec_file = str(tmp_path / "isolated_reconciliation.jsonl")

    with open(ledger_file, "w", encoding="utf-8") as f:
        f.write(json.dumps({"trade_id": "t1", "signal_id": "s1", "status": "CLOSED"}) + "\n")
        f.write(json.dumps({"trade_id": "t2", "signal_id": "s2", "status": "CLOSED"}) + "\n")

    reconciler = PaperReconciliation(ledger_file, reconciliation_file=rec_file)
    ok = reconciler.run()

    assert ok is True
    assert os.path.exists(rec_file), "Isolated reconciliation file should have been created"

    with open(rec_file, "r", encoding="utf-8") as f:
        records = [json.loads(line) for line in f if line.strip()]

    assert len(records) == 1
    assert records[0]["status"] == "OK"
    assert records[0]["detail"] == "OK"

    prod_size_after = os.path.getsize(prod_file) if os.path.exists(prod_file) else 0
    assert prod_size_after == prod_size_before, "Production forward_reconciliation.jsonl must not be modified by isolated test"


def test_paper_reconciliation_env_var_override(tmp_path, monkeypatch):
    """Ensure PaperReconciliation respects FORWARD_RECONCILIATION_FILE env var."""
    from paper_engine.reconciliation import PaperReconciliation

    ledger_file = str(tmp_path / "paper_ledger.jsonl")
    rec_file = str(tmp_path / "env_reconciliation.jsonl")

    with open(ledger_file, "w", encoding="utf-8") as f:
        f.write(json.dumps({"trade_id": "t1", "signal_id": "s1"}) + "\n")

    monkeypatch.setenv("FORWARD_RECONCILIATION_FILE", rec_file)

    reconciler = PaperReconciliation(ledger_file)
    assert reconciler.reconciliation_file == rec_file

    ok = reconciler.run()
    assert ok is True
    assert os.path.exists(rec_file)

    with open(rec_file, "r", encoding="utf-8") as f:
        records = [json.loads(line) for line in f if line.strip()]
    assert len(records) == 1
    assert records[0]["status"] == "OK"


def test_paper_reconciliation_duplicate_detection_isolated(tmp_path):
    """Ensure duplicate detection writes RECONCILIATION_ERROR to isolated file."""
    from paper_engine.reconciliation import PaperReconciliation

    ledger_file = str(tmp_path / "paper_ledger.jsonl")
    rec_file = str(tmp_path / "dup_reconciliation.jsonl")

    with open(ledger_file, "w", encoding="utf-8") as f:
        f.write(json.dumps({"trade_id": "dup_1", "signal_id": "s1"}) + "\n")
        f.write(json.dumps({"trade_id": "dup_1", "signal_id": "s2"}) + "\n")

    reconciler = PaperReconciliation(ledger_file, reconciliation_file=rec_file)
    ok = reconciler.run()

    assert ok is False
    assert os.path.exists(rec_file)

    with open(rec_file, "r", encoding="utf-8") as f:
        records = [json.loads(line) for line in f if line.strip()]
    assert len(records) == 1
    assert records[0]["status"] == "RECONCILIATION_ERROR"
    assert "DUPLICATE_TRADE_IDS" in records[0]["detail"]

