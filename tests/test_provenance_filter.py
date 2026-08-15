import os
import json
import pytest
from dashboard import app

@pytest.fixture
def client():
    app.config['TESTING'] = True
    with app.test_client() as client:
        yield client

def test_provenance_filter_excludes_synthetic_test_records(client, tmp_path, monkeypatch):
    """Proves that source=TEST records are strictly excluded from /api/trades in TESTNET mode."""
    ledger_file = tmp_path / "testnet_trade_ledger.jsonl"
    monkeypatch.setenv("TESTNET_LEDGER_FILE", str(ledger_file))
    monkeypatch.setattr("config.TRADING_MODE", "TESTNET")
    
    # Write a mix of synthetic and real records
    records = [
        {"timestamp": "2026-08-15T19:00:49Z", "symbol": "BTCUSDT", "strategy": "TEST", "source": "TEST", "action": "CLOSE_WIN", "quantity": 0.5, "entry_price": 50000.0, "exit_price": 52000.0, "pnl": 974.0, "net_pnl": 974.0, "entry_order_id": None, "exit_order_id": None},
        {"timestamp": "2026-08-14T11:00:35Z", "symbol": "BTCUSDT", "strategy": "RECOVERED", "source": "RECOVERY_FROM_BINANCE", "action": "CLOSED_LOSS", "quantity": 0.001, "entry_price": 63317.87, "exit_price": 63350.0, "pnl": -0.0321, "net_pnl": -0.0321, "entry_order_id": "2920255", "exit_order_id": "2920974"},
        {"timestamp": "2026-08-14T11:34:09Z", "symbol": "BTCUSDT", "strategy": "ADX_EMA", "source": "BINANCE_EXECUTION", "action": "CLOSED_WIN", "quantity": 0.001, "entry_price": 63345.99, "exit_price": 63333.33, "pnl": 0.0126, "net_pnl": 0.0126, "entry_order_id": "2921714", "exit_order_id": "2926294"}
    ]
    
    with open(ledger_file, "w") as f:
        for r in records:
            f.write(json.dumps(r) + "\n")
            
    res = client.get("/api/trades")
    assert res.status_code == 200
    data = res.get_json()
    positions = data.get("positions", [])
    closed = [p for p in positions if p.get("status") == "CLOSED"]
    
    # The synthetic +974.00 trade MUST BE EXCLUDED
    pnls = [p["pnl"] for p in closed]
    assert 974.0 not in pnls
    assert len(closed) == 2
    assert closed[0]["source"] in ["RECOVERY_FROM_BINANCE", "BINANCE_EXECUTION"]
    assert closed[1]["source"] in ["RECOVERY_FROM_BINANCE", "BINANCE_EXECUTION"]
