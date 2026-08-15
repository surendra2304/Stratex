import os
from testnet_engine.discovery import SymbolDiscoveryService

def check_discovery():
    try:
        service = SymbolDiscoveryService(testnet=True)
        symbols = service.discover_eligible_symbols(min_quote_volume=1_000_000)
        print(f"Number of eligible symbols discovered: {len(symbols)}")
        print(f"Top 5: {symbols[:5]}")
        print("Market-data connection status: CONNECTED")
    except Exception as e:
        print(f"Connection failed: {e}")
        print("Market-data connection status: OFFLINE")

if __name__ == "__main__":
    check_discovery()
