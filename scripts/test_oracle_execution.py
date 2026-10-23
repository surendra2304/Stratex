#!/usr/bin/env python3
"""
Test script to verify cTrader FIX connection, symbol aliasing, and AI Advisor on Oracle Cloud.
"""
import os
import sys

# Ensure Stratex path is loaded
sys.path.insert(0, "/opt/friday_universe/Stratex")
os.chdir("/opt/friday_universe/Stratex")

from dotenv import load_dotenv
load_dotenv("/opt/friday_universe/Stratex/.env")

from exchanges.ctrader_fix_adapter import CTraderFixAdapter
from autonomy.ai_advisor import StratexAIAdvisor

print("--- 1. Testing CTraderFixAdapter ---")
adapter = CTraderFixAdapter()
print(f"Loaded symbol map size: {len(adapter.symbol_map)}")

for test_sym in ["GER40", "USTEC", "US30", "BTCUSD", "ETHUSD", "XAUUSD"]:
    canonical, sid = adapter.resolve_symbol(test_sym)
    units = adapter.calculate_units(canonical, 0.01) if sid else "N/A"
    check = adapter.order_check(test_sym, "BUY", "MARKET", 0.01)
    print(f"Symbol: {test_sym:7} -> Canonical: {canonical:7} | ID: {str(sid):6} | Units: {units:6} | Check Approved: {check.get('approved')}")

print("\n--- 2. Testing Live Positions via FIX (35=AN) ---")
try:
    positions = adapter.get_positions()
    print(f"Open Positions count: {len(positions)}")
    for pos in positions:
        print(f"  -> {pos.symbol} {pos.side} qty={pos.quantity} entry={pos.entry_price} pos_id={pos.position_id}")
except Exception as e:
    print(f"Error querying positions: {e}")

print("\n--- 3. Testing AI Advisor ---")
advisor = StratexAIAdvisor()
verdict = advisor.evaluate_trade_candidate(
    symbol="BTCUSD",
    timeframe="15m",
    action="BUY",
    current_price=81200.0,
    stop_loss=80800.0,
    take_profit=81800.0,
    risk_reward_ratio=1.5,
    indicators={"rsi": 32.5, "kalman_z": -2.1, "regime": "MEAN_REVERTING"},
)
print(f"AI Advisor Verdict: Approved={verdict.approved} | Verdict={verdict.verdict} | Conf={verdict.confidence}")
print(f"Thesis: {verdict.thesis}")
print(f"Model used: {verdict.model_used}")
