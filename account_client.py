# ==============================================================================
# ACCOUNT_CLIENT.PY - Read-Only Account Diagnostics Client
# ==============================================================================
# IMPORTANT: This client is for READ-ONLY account diagnostics only.
# It must NOT expose order execution methods.
# It is SEPARATE from ExecutionClient and MarketDataClient.
# ==============================================================================
import logging
import time

from binance.client import Client
from binance.exceptions import BinanceAPIException

from config import API_KEY, SECRET_KEY, TRADING_MODE

logger = logging.getLogger("account_client")

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
            try:
                return func(*args, **kwargs)
            except Exception as retry_err:
                logger.error(f"[RATE_LIMIT_RETRY_FAILED] Call failed after 60s pause: {retry_err}")
                return {}
        raise
    except Exception as e:
        if "429" in str(e) or "418" in str(e):
            logger.critical(f"[RATE_LIMIT_HIT] 🚨 Rate Limit in Exception: {e}. Pausing all requests for 60s...")
            _LAST_429_PAUSE_UNTIL = time.time() + 60.0
            time.sleep(60.0)
        raise


class AccountClient:
    """
    A strictly read-only adapter for Binance account data.
    Only exposes account-read operations (balances, open positions, etc.).
    Does NOT expose order placement, cancellation, or withdrawal methods.

    Architecture:
        AccountClient  → account-read only (balances, etc.)
        MarketDataClient → market-read only (candles, tickers, funding)
        ExecutionClient  → order execution (ExecutionPolicy gated)
    """

    def __init__(self):
        if TRADING_MODE == "PAPER":
            self._client = None
        else:
            # Testnet for non-PAPER modes.
            # For LIVE, config.py must supply production credentials.
            testnet = TRADING_MODE != "LIVE"
            self._client = Client(API_KEY, SECRET_KEY, testnet=testnet)

    def is_available(self) -> bool:
        return self._client is not None

    # --- Explicitly Approved Account-Read Methods ---

    def get_account(self) -> dict:
        """Returns full account info."""
        if not self.is_available():
            return {}
        return _execute_with_rate_limit_protection(self._client.get_account) or {}

    def get_balances(self) -> dict:
        """Returns non-zero free balances as {asset: float}."""
        if not self.is_available():
            return {}
        account = _execute_with_rate_limit_protection(self._client.get_account) or {}
        return {
            b["asset"]: float(b["free"])
            for b in account.get("balances", [])
            if float(b["free"]) > 0
        }

    def get_open_orders(self, symbol: str | None = None) -> list:
        """Returns exchange-side open orders (read-only query)."""
        if not self.is_available():
            return []
        if symbol:
            return _execute_with_rate_limit_protection(self._client.get_open_orders, symbol=symbol) or []
        return _execute_with_rate_limit_protection(self._client.get_open_orders) or []

    def futures_account(self) -> dict:
        """Returns Futures account info (/fapi/v2/account)."""
        if not self.is_available():
            return {}
        return _execute_with_rate_limit_protection(self._client.futures_account) or {}

    def futures_position_information(self, symbol: str | None = None) -> list:
        """Returns Futures position info."""
        if not self.is_available():
            return []
        if symbol:
            return _execute_with_rate_limit_protection(self._client.futures_position_information, symbol=symbol) or []
        return _execute_with_rate_limit_protection(self._client.futures_position_information) or []

    # --- Block all other Binance Client methods ---
    def __getattr__(self, item):
        # Deny any attribute not explicitly defined above
        blocked = {
            "create_order", "cancel_order", "create_oco_order",
            "withdraw", "transfer", "create_margin_order"
        }
        if item in blocked or (hasattr(Client, item) and "order" in item.lower()):
            raise AttributeError(
                f"AccountClient strictly prohibits execution method: '{item}'. "
                "Use ExecutionClient via get_exchange_client() for order operations."
            )
        raise AttributeError(f"'AccountClient' object has no attribute '{item}'")
