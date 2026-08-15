import sys
import os

def main():
    print("=" * 60)
    print("  ALGORITHMIC TRADING BOT FRAMEWORK")
    print("  [ACTIVE] TESTNET SERVICE")
    print("=" * 60)
    
    from config import TRADING_MODE
    if TRADING_MODE == "TESTNET":
        from testnet_engine.service import TestnetService
        service = TestnetService()
        try:
            service.run()
        except KeyboardInterrupt:
            print("\n[BOT] Stopped by user.")
    else:
        print(f"TRADING_MODE {TRADING_MODE} is not supported by this entrypoint. Please use testnet_engine/service.py directly or configure TRADING_MODE=TESTNET.")

if __name__ == "__main__":
    main()
