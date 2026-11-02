#!/usr/bin/env python
"""Test portfolio hub directly - this is what /balance /pnl /positions use"""
import sys
sys.stdout.reconfigure(encoding='utf-8')

print("Testing unified_portfolio_hub...")
try:
    from exchanges.unified_portfolio_hub import get_portfolio_hub
    hub = get_portfolio_hub()
    print("Hub created OK")

    print("\n--- get_combined_balance ---")
    try:
        comb = hub.get_combined_balance()
        print("OK:", comb)
    except Exception as e:
        import traceback; traceback.print_exc()

    print("\n--- get_combined_pnl ---")
    try:
        pnl = hub.get_combined_pnl()
        print("OK:", pnl)
    except Exception as e:
        import traceback; traceback.print_exc()

    print("\n--- get_combined_positions ---")
    try:
        pos = hub.get_combined_positions()
        print("OK count:", len(pos))
    except Exception as e:
        import traceback; traceback.print_exc()

except Exception as e:
    import traceback; traceback.print_exc()