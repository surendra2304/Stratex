"""Binance venue agent and environment-safe client factory.

The legacy Stratex trading lifecycle still uses python-binance's Futures methods.
This agent isolates exchange credentials/endpoints from the strategy and risk core.
It supports the current Binance Demo Trading API when explicitly enabled and keeps
the legacy Futures Testnet as a separate, explicit compatibility mode.

Never falls back from Demo credentials to legacy keys: those credentials are not
interchangeable. Live endpoints are never selected by this factory.
"""
from __future__ import annotations

import os
from typing import Any

from binance.client import Client


BINANCE_DEMO_SPOT_API_URL = "https://demo-api.binance.com/api"
BINANCE_DEMO_FUTURES_API_URL = "https://demo-fapi.binance.com/fapi"
BINANCE_LEGACY_SPOT_TESTNET_URL = "https://testnet.binance.vision/api"
BINANCE_LEGACY_FUTURES_TESTNET_URL = "https://testnet.binancefuture.com/fapi"
_PLACEHOLDERS = {"", "YOUR_BINANCE_API_KEY", "YOUR_BINANCE_API_SECRET",
                 "YOUR_BINANCE_TESTNET_API_KEY", "YOUR_BINANCE_TESTNET_API_SECRET",
                 "YOUR_API_KEY", "YOUR_API_SECRET"}


def _truthy(value: str | None) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes", "on"}


def _real_secret(value: str | None) -> bool:
    return bool(value and value.strip() and value.strip() not in _PLACEHOLDERS)


class BinanceFuturesAgent:
    """Isolated Binance client lifecycle for Stratex's test/demo runtime."""

    agent_id = "binance_futures"

    def __init__(self, demo_trading: bool | None = None):
        self.demo_trading = _truthy(os.getenv("BINANCE_DEMO_TRADING", "false")) if demo_trading is None else bool(demo_trading)

    def _assert_safe_mode(self, mode: str) -> None:
        normalized = str(mode).upper()
        if normalized not in {"TESTNET", "FUTURES"}:
            raise RuntimeError(f"Binance execution agent refuses TRADING_MODE={normalized!r}; expected TESTNET or FUTURES.")
        if _truthy(os.getenv("LIVE_TRADING_ENABLED", "false")):
            raise RuntimeError("Binance execution agent refuses LIVE_TRADING_ENABLED=true.")
        if normalized == "PAPER":
            raise RuntimeError("Paper mode must not construct an exchange execution client.")

    def create_client(
        self,
        mode: str,
        *,
        legacy_api_key: str = "",
        legacy_secret_key: str = "",
        client_cls: Any = None,
    ) -> Any:
        """Create a signed client pinned to demo/testnet URLs only."""
        self._assert_safe_mode(mode)
        client_type = client_cls or Client
        if self.demo_trading:
            # Deliberately do not fallback to API_KEY/SECRET_KEY: those may belong
            # to a different, legacy testnet or account.
            api_key = os.getenv("BINANCE_API_KEY", "").strip().strip("'\"")
            api_secret = os.getenv("BINANCE_API_SECRET", "").strip().strip("'\"")
            if not _real_secret(api_key) or not _real_secret(api_secret):
                raise RuntimeError(
                    "BINANCE_DEMO_TRADING=true requires fresh Demo Trading credentials in "
                    "BINANCE_API_KEY and BINANCE_API_SECRET. Legacy API_KEY/SECRET_KEY are not used."
                )
            client = client_type(api_key, api_secret, testnet=False, ping=False)
            client.API_URL = BINANCE_DEMO_SPOT_API_URL
            client.FUTURES_URL = BINANCE_DEMO_FUTURES_API_URL
            # Explicit audit markers; not used to choose endpoint URLs downstream.
            client.stratex_venue = "binance"
            client.stratex_environment = "demo_trading"
        else:
            api_key = (legacy_api_key or os.getenv("API_KEY", "")).strip()
            api_secret = (legacy_secret_key or os.getenv("SECRET_KEY", "")).strip()
            if not _real_secret(api_key) or not _real_secret(api_secret):
                raise RuntimeError(
                    "Legacy Binance Testnet mode requires API_KEY/SECRET_KEY (or the legacy testnet aliases)."
                )
            client = client_type(api_key, api_secret, testnet=True, ping=False)
            client.API_URL = BINANCE_LEGACY_SPOT_TESTNET_URL
            client.FUTURES_URL = BINANCE_LEGACY_FUTURES_TESTNET_URL
            client.stratex_venue = "binance"
            client.stratex_environment = "legacy_futures_testnet"
        self._assert_no_production_endpoints(client)
        return client

    def create_public_client(self, *, futures: bool = True) -> Any:
        """Create an unauthenticated market-data client pinned to demo/testnet."""
        client = Client("", "", testnet=not self.demo_trading, ping=False)
        if self.demo_trading:
            client.API_URL = BINANCE_DEMO_SPOT_API_URL
            client.FUTURES_URL = BINANCE_DEMO_FUTURES_API_URL
            client.stratex_environment = "demo_trading_public"
        else:
            client.API_URL = BINANCE_LEGACY_SPOT_TESTNET_URL
            client.FUTURES_URL = BINANCE_LEGACY_FUTURES_TESTNET_URL
            client.stratex_environment = "legacy_testnet_public"
        client.stratex_venue = "binance"
        self._assert_no_production_endpoints(client)
        return client

    @staticmethod
    def _assert_no_production_endpoints(client: Any) -> None:
        """Fail closed if a client endpoint accidentally points at production."""
        api_url = str(getattr(client, "API_URL", ""))
        futures_url = str(getattr(client, "FUTURES_URL", ""))
        allowed_api = {BINANCE_DEMO_SPOT_API_URL, BINANCE_LEGACY_SPOT_TESTNET_URL}
        allowed_futures = {BINANCE_DEMO_FUTURES_API_URL, BINANCE_LEGACY_FUTURES_TESTNET_URL}
        if api_url not in allowed_api or futures_url not in allowed_futures:
            raise RuntimeError("Binance agent rejected unrecognized/non-demo endpoint configuration.")
