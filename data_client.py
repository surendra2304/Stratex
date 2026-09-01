# ==============================================================================
# DATA_CLIENT.PY - Read-Only Market Data Client
# ==============================================================================
# CRITICAL SECURITY NOTE:
#   This client connects to Binance PUBLIC/TESTNET endpoints for MARKET DATA ONLY.
#   It does NOT use trading credentials (API_KEY / SECRET_KEY) for data requests.
#   Binance public kline/ticker endpoints do NOT require authentication.
#   Do NOT pass API_KEY or SECRET_KEY to this client.
# ==============================================================================
import logging
import time

from binance.client import Client
from binance.exceptions import BinanceAPIException

from config import TRADING_MODE

logger = logging.getLogger("data_client")

# Approved read-only methods that this adapter may proxy
_APPROVED_METHODS = frozenset([
    "get_ticker",
    "get_symbol_ticker",
    "get_klines",
    "get_historical_klines",
    "futures_funding_rate",
    "get_exchange_info",
    "get_orderbook_tickers",
    "futures_klines",
    "futures_historical_klines",
    "futures_exchange_info",
    "futures_symbol_ticker",
    "futures_ticker",
    "futures_mark_price",
])

# Global rate limit tracker across instances
_LAST_429_PAUSE_UNTIL = 0.0


def _execute_with_rate_limit_protection(func, *args, **kwargs):
    """Executes a Binance API call with automatic 429/418 rate-limit protection."""
    global _LAST_429_PAUSE_UNTIL
    now = time.time()
    if now < _LAST_429_PAUSE_UNTIL:
        wait_left = _LAST_429_PAUSE_UNTIL - now
        logger.warning(f"[RATE_LIMIT_SAFETY] In active 429 backoff. Pausing API call for {wait_left:.1f}s...")
        time.sleep(min(wait_left, 60.0))

    try:
        return func(*args, **kwargs)
    except BinanceAPIException as e:
        if e.status_code in (429, 418) or "429" in str(e) or "418" in str(e) or "Way too many requests" in str(e):
            logger.critical(f"[RATE_LIMIT_HIT] 🚨 Binance API {e.status_code} Rate Limit Hit! Pausing all requests for 60s...")
            _LAST_429_PAUSE_UNTIL = time.time() + 60.0
            time.sleep(60.0)
            # Retry once after cooling off
            try:
                return func(*args, **kwargs)
            except Exception as retry_err:
                logger.error(f"[RATE_LIMIT_RETRY_FAILED] Call failed after 60s pause: {retry_err}")
                return None
        raise
    except Exception as e:
        if "429" in str(e) or "418" in str(e):
            logger.critical(f"[RATE_LIMIT_HIT] 🚨 Rate Limit in Exception: {e}. Pausing all requests for 60s...")
            _LAST_429_PAUSE_UNTIL = time.time() + 60.0
            time.sleep(60.0)
        raise


class MarketDataClient:
    """
    A strictly read-only adapter for Binance market data (Spot & Futures).
    Provides candles, tickers, funding rates — no account or execution access.

    Security guarantees:
      - Does NOT accept API credentials.
      - Does NOT expose _client directly.
      - Only proxies an explicit whitelist of read-only methods.
      - Raises AttributeError for all other Binance Client methods.
      - PAPER mode returns is_available()=False (DATA_UNAVAILABLE).
      - Non-PAPER modes use Testnet public endpoints (no authentication required).

    Data source labels:
      BINANCE_TESTNET_READ_ONLY  — Testnet public endpoint
      DATA_UNAVAILABLE           — PAPER mode or connection disabled
    """

    def __init__(self):
        if TRADING_MODE == "PAPER":
            self.__client = None
            self.data_source = "DATA_UNAVAILABLE"
        else:
            # Use no credentials — public market-data endpoints do not require auth.
            # Testnet by default; for LIVE mode, still use testnet for data reads
            # unless a separate production data source is configured.
            self.__client = Client("", "", testnet=True)
            self.data_source = "BINANCE_TESTNET_READ_ONLY"

    def is_available(self) -> bool:
        return self.__client is not None

    # --- Explicitly Approved Read-Only Market Methods ---

    def get_ticker(self, **kwargs):
        """All symbol 24hr ticker price change statistics."""
        if not self.is_available():
            return None
        return _execute_with_rate_limit_protection(self.__client.get_ticker, **kwargs)

    def get_symbol_ticker(self, **kwargs):
        """Latest price for a symbol."""
        if not self.is_available():
            return None
        return _execute_with_rate_limit_protection(self.__client.get_symbol_ticker, **kwargs)

    def get_klines(self, **kwargs):
        """Kline/Candlestick data for a symbol."""
        if not self.is_available():
            return None
        return _execute_with_rate_limit_protection(self.__client.get_klines, **kwargs)

    def get_historical_klines(self, symbol, interval, start_str, end_str=None, **kwargs):
        """Historical klines (candles) for a symbol."""
        if not self.is_available():
            return None
        return _execute_with_rate_limit_protection(self.__client.get_historical_klines, symbol, interval, start_str, end_str, **kwargs)

    def futures_funding_rate(self, **kwargs):
        """Get funding rate history."""
        if not self.is_available():
            return None
        return _execute_with_rate_limit_protection(self.__client.futures_funding_rate, **kwargs)

    def get_exchange_info(self, **kwargs):
        """Current exchange trading rules and symbol information."""
        if not self.is_available():
            return None
        return _execute_with_rate_limit_protection(self.__client.get_exchange_info, **kwargs)

    def futures_klines(self, **kwargs):
        """Futures klines/candlestick data for a symbol (/fapi/v1/klines)."""
        if not self.is_available():
            return None
        return _execute_with_rate_limit_protection(self.__client.futures_klines, **kwargs)

    def futures_historical_klines(self, symbol, interval, start_str, end_str=None, **kwargs):
        """Historical futures klines for a symbol."""
        if not self.is_available():
            return None
        return _execute_with_rate_limit_protection(self.__client.futures_historical_klines, symbol, interval, start_str, end_str, **kwargs)

    def futures_exchange_info(self, **kwargs):
        """Current futures exchange trading rules and symbol information (/fapi/v1/exchangeInfo)."""
        if not self.is_available():
            return None
        return _execute_with_rate_limit_protection(self.__client.futures_exchange_info, **kwargs)

    def futures_symbol_ticker(self, **kwargs):
        """Latest futures price for a symbol (/fapi/v1/ticker/price)."""
        if not self.is_available():
            return None
        return _execute_with_rate_limit_protection(self.__client.futures_symbol_ticker, **kwargs)

    def futures_ticker(self, **kwargs):
        """24-hour futures ticker price change statistics (/fapi/v1/ticker/24hr)."""
        if not self.is_available():
            return None
        return _execute_with_rate_limit_protection(self.__client.futures_ticker, **kwargs)

    def futures_mark_price(self, **kwargs):
        """Latest futures mark price and funding rate (/fapi/v1/premiumIndex)."""
        if not self.is_available():
            return None
        return _execute_with_rate_limit_protection(self.__client.futures_mark_price, **kwargs)

    # --- Explicit Block: No other methods allowed ---
    def __getattr__(self, item):
        """Deny access to any Binance Client method not in the approved whitelist."""
        if item in _APPROVED_METHODS:
            # Should never reach here since approved methods are defined above
            raise AttributeError(f"Internal error: approved method '{item}' not implemented.")
        if hasattr(Client, item):
            raise AttributeError(
                f"MarketDataClient strictly prohibits access to '{item}'. "
                "This client is READ-ONLY market data. "
                "For account access use AccountClient. "
                "For execution use get_exchange_client()."
            )
        raise AttributeError(f"'MarketDataClient' object has no attribute '{item}'")
