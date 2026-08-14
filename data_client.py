# ==============================================================================
# DATA_CLIENT.PY - Read-Only Market Data Client
# ==============================================================================
from binance.client import Client
from config import API_KEY, SECRET_KEY, TRADING_MODE

class MarketDataClient:
    """
    A read-only adapter for the Binance Client.
    Provides strictly market data (candles, ticker, funding rates).
    Guaranteed not to expose order execution methods.
    """
    def __init__(self):
        # Determine if we can instantiate a raw client for public data
        if TRADING_MODE == "PAPER":
            # For strict PAPER mode we don't connect to Binance at all 
            # unless a specific synthetic/test mode is enabled.
            # Returning an explicit DATA_UNAVAILABLE concept.
            self._client = None
            self.data_source = "DATA_UNAVAILABLE"
        else:
            # We instantiate a purely public/testnet client for read-only access.
            # We default to testnet endpoints so as not to exhaust live limits unnecessarily
            # but allow reading public data.
            self._client = Client(API_KEY, SECRET_KEY, testnet=True)
            self.data_source = "BINANCE_READ_ONLY"

    def is_available(self):
        return self._client is not None

    # --- Approved Read Methods ---
    
    def get_ticker(self, **kwargs):
        if not self.is_available():
            return None
        return self._client.get_ticker(**kwargs)
        
    def get_symbol_ticker(self, **kwargs):
        if not self.is_available():
            return None
        return self._client.get_symbol_ticker(**kwargs)
        
    def get_klines(self, **kwargs):
        if not self.is_available():
            return None
        return self._client.get_klines(**kwargs)
        
    def get_historical_klines(self, **kwargs):
        if not self.is_available():
            return None
        return self._client.get_historical_klines(**kwargs)
        
    def futures_funding_rate(self, **kwargs):
        if not self.is_available():
            return None
        return self._client.futures_funding_rate(**kwargs)

    # Do not proxy any other methods (especially create_order, cancel_order, etc)
    def __getattr__(self, item):
        if hasattr(Client, item):
            raise AttributeError(f"MarketDataClient strictly prohibits access to Binance Client method: {item}")
        raise AttributeError(f"'MarketDataClient' object has no attribute '{item}'")
