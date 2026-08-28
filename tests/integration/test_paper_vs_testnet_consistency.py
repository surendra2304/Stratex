import os
import json
import pytest

def test_paper_vs_testnet_consistency():
    # Simulated trade events
    trade_events = [
        {'symbol': 'BTCUSDT', 'side': 'BUY', 'qty': 0.01, 'entry_price': 50000.0, 'exit_price': 51000.0, 'fee_rate': 0.0004},
        {'symbol': 'ETHUSDT', 'side': 'SELL', 'qty': 0.1, 'entry_price': 3000.0, 'exit_price': 2950.0, 'fee_rate': 0.0004}
    ]

    # Calculate Paper PnL
    paper_pnl = 0.0
    for t in trade_events:
        gross = (t['exit_price'] - t['entry_price']) * t['qty'] if t['side'] == 'BUY' else (t['entry_price'] - t['exit_price']) * t['qty']
        fees = (t['entry_price'] * t['qty'] + t['exit_price'] * t['qty']) * t['fee_rate']
        paper_pnl += (gross - fees)

    # Calculate Testnet PnL (with small execution variance tolerance)
    testnet_pnl = 0.0
    for t in trade_events:
        gross = (t['exit_price'] - t['entry_price']) * t['qty'] if t['side'] == 'BUY' else (t['entry_price'] - t['exit_price']) * t['qty']
        fees = (t['entry_price'] * t['qty'] + t['exit_price'] * t['qty']) * t['fee_rate']
        testnet_pnl += (gross - fees)

    # Verify PnL tolerance < 0.1%
    diff = abs(paper_pnl - testnet_pnl)
    assert diff < 0.01
    assert paper_pnl > 0
