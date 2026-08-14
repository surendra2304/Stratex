#!/usr/bin/env python3
"""
test_connection.py - Diagnostic script to verify connectivity.
Uses AccountClient for read-only account diagnostics.
No hardcoded credentials. Reads from config (loaded from .env).
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from account_client import AccountClient
from data_client import MarketDataClient

def run_connection_test():
    print("=" * 50)
    print("CONNECTION DIAGNOSTIC (READ-ONLY)")
    print("=" * 50)

    # 1. Market data check
    try:
        mdc = MarketDataClient()
        if mdc.is_available():
            ticker = mdc.get_symbol_ticker(symbol="BTCUSDT")
            print(f"[MARKET] OK | BTCUSDT: {ticker['price']}")
        else:
            print(f"[MARKET] DATA_UNAVAILABLE (PAPER mode or no data source)")
    except Exception as e:
        print(f"[MARKET] FAILED: {e}")

    # 2. Account read check (separate client — no order privileges)
    try:
        acc = AccountClient()
        if acc.is_available():
            balances = acc.get_balances()
            print(f"[ACCOUNT] OK | Non-zero balances: {len(balances)}")
        else:
            print(f"[ACCOUNT] UNAVAILABLE (PAPER mode)")
    except Exception as e:
        print(f"[ACCOUNT] FAILED: {e}")

if __name__ == "__main__":
    run_connection_test()
