# ==============================================================================
# ACCOUNT_CLIENT.PY - Read-Only Account Diagnostics Client
# ==============================================================================
# IMPORTANT: This client is for READ-ONLY account diagnostics only.
# It must NOT expose order execution methods.
# It is SEPARATE from ExecutionClient and MarketDataClient.
# ==============================================================================
from binance.client import Client

from config import API_KEY, SECRET_KEY, TRADING_MODE


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
        return self._client.get_account()

    def get_balances(self) -> dict:
        """Returns non-zero free balances as {asset: float}."""
        if not self.is_available():
            return {}
        account = self._client.get_account()
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
            return self._client.get_open_orders(symbol=symbol)
        return self._client.get_open_orders()

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
