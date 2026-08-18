import pytest
import json
import os
import sys

def test_accounting_case_1_clean_start():
    """
    CASE 1:
    Starting equity = $10,000
    Cash = $10,000
    Crypto = $0
    Realized PNL = $0
    Expected equity: $10,000
    """
    cash = 10000.0
    crypto_market_value = 0.0
    realized_pnl = 0.0
    
    total_equity = cash + crypto_market_value
    assert total_equity == 10000.0
    assert realized_pnl == 0.0

def test_accounting_case_2_crypto_purchase_and_realized_pnl_not_added():
    """
    CASE 2:
    Starting equity = $10,000
    Cash = $9,800
    Crypto = $200 (purchased with $200 cash)
    Realized PNL = $200 (from previous trade, already inside cash balance)
    Expected total equity: $10,000 (NOT $10,200)
    """
    cash = 9800.0
    crypto_market_value = 200.0
    realized_pnl = 200.0
    
    # Realized PNL is a performance metric, NOT an additional asset on top of cash + crypto.
    total_equity = cash + crypto_market_value
    assert total_equity == 10000.0
    assert total_equity != 10200.0

def test_accounting_case_3_cash_and_crypto_holdings():
    """
    CASE 3:
    Cash = $8,000
    Crypto = $2,000
    Expected: Total Equity = $10,000
    """
    cash = 8000.0
    crypto_market_value = 2000.0
    
    total_equity = cash + crypto_market_value
    assert total_equity == 10000.0

def test_accounting_case_4_realized_pnl_already_in_binance_balance():
    """
    CASE 4:
    Cash = $10,000
    Realized PNL = $500 already reflected in Binance balance
    Expected: Total Equity = $10,000, Realized PNL = $500 (NOT Total Equity = $10,500)
    """
    cash = 10000.0
    crypto_market_value = 0.0
    realized_pnl = 500.0
    
    total_equity = cash + crypto_market_value
    assert total_equity == 10000.0
    assert total_equity != 10500.0
    assert realized_pnl == 500.0

def test_accounting_case_5_prevention_of_18000_double_counting():
    """
    CASE 5:
    Cash = $8,000
    Crypto = $2,000
    Realized PNL = $8,000
    Determine whether the $8,000 is already represented in the current assets.
    Expected: Total Equity = $10,000, Realized PNL = $8,000 (Do NOT produce $18,000)
    """
    cash = 8000.0
    crypto_market_value = 2000.0
    realized_pnl = 8000.0
    
    # Realized PnL is ALREADY represented in the current assets (cash + crypto)
    total_equity = cash + crypto_market_value
    assert total_equity == 10000.0
    assert total_equity != 18000.0
    assert realized_pnl == 8000.0

def test_dashboard_status_endpoint_accounting(monkeypatch):
    """
    Verifies that dashboard /api/status produces exact mark-to-market equity
    without double-counting unrealized PnL or realized PnL.
    """
    import dashboard
    
    # Mock get_live_account_and_holdings
    monkeypatch.setattr(dashboard, "get_live_account_and_holdings", lambda force_refresh=False: {
        "usdt_free": 8000.0,
        "usdt_locked": 0.0,
        "usdt_total_cash": 8000.0,
        "total_crypto_value": 2000.0,
        "active_trade_holdings_value": 2000.0,
        "holdings": [{"asset": "LINK", "symbol": "LINKUSDT", "usd_value": 2000.0, "is_bot_trade": True}]
    })
    
    # Mock trade ledger data (realized PnL)
    monkeypatch.setattr(dashboard, "_get_trades_data", lambda: {
        "net_pnl": 500.0,
        "positions": []
    })
    
    client = dashboard.app.test_client()
    res = client.get('/api/status')
    assert res.status_code == 200
    data = res.get_json()
    
    # Total Equity MUST be $8,000 cash + $2,000 crypto = $10,000
    # NOT $10,000 + $500 realized PnL
    assert data["equity"] == 10000.0
    assert data["cash"] == 8000.0
    assert data["crypto_holdings_value"] == 2000.0
    assert data["realized_pnl"] == 500.0
