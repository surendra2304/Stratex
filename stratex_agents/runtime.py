"""Explicit, fail-closed factory for Stratex's shared order manager.

Nothing in this module starts trading automatically. A caller must explicitly invoke
build_order_manager with a deterministic authorization callback; the global execution
mode must be DEMO; venue allowlisting and demo-only environment checks are mandatory.
"""
from __future__ import annotations

import os
from typing import Callable

from .oms import StratexOrderManager
from .order_intent import TradeIntent
from .order_store import OrderIntentStore


def _enabled(name: str, default: str = "false") -> bool:
    return os.getenv(name, default).strip().lower() in {"1", "true", "yes", "on"}


def build_order_manager(
    authorize_intent: Callable[[TradeIntent], tuple[bool, str]],
    *,
    store: OrderIntentStore | None = None,
    binance_adapter=None,
    mt5_adapter=None,
    check_mt5_health: bool = True,
) -> StratexOrderManager:
    """Build an OMS using only explicitly allowlisted non-production venues.

    The supplied authorization callback must be a deterministic Stratex risk/policy
    check. Do not supply a callback that accepts a raw LLM recommendation.
    """
    if not callable(authorize_intent):
        raise TypeError("authorize_intent must be a callable deterministic risk decision")
    if _enabled("LIVE_TRADING_ENABLED"):
        raise PermissionError("Live-money trading is disabled.")
    if os.getenv("TRADING_MODE", "PAPER").strip().upper() not in {"TESTNET", "FUTURES"}:
        raise PermissionError("Set the existing TRADING_MODE to TESTNET/FUTURES before building demo execution.")
    if os.getenv("STRATEX_EXECUTION_MODE", "DISABLED").strip().upper() != "DEMO":
        raise PermissionError("STRATEX_EXECUTION_MODE must be explicitly set to DEMO.")
    allowed = {
        item.strip().lower()
        for item in os.getenv("STRATEX_ALLOWED_EXECUTION_VENUES", "binance").split(",")
        if item.strip()
    }
    if not allowed.issubset({"binance", "mt5"}):
        raise ValueError("STRATEX_ALLOWED_EXECUTION_VENUES supports only binance and mt5 in this release.")

    venues = {}
    if "binance" in allowed:
        from .venue.binance_futures_oms_adapter import BinanceFuturesOMSAdapter
        venues["binance"] = binance_adapter or BinanceFuturesOMSAdapter()

    if "mt5" in allowed:
        if os.getenv("TRADING_MODE", "").strip().upper() != "FUTURES":
            raise PermissionError("MT5 demo routing requires TRADING_MODE=FUTURES.")
        if not _enabled("MT5_BRIDGE_ENABLED") or not _enabled("MT5_REQUIRE_DEMO", "true"):
            raise PermissionError("MT5 must be explicitly enabled and MT5_REQUIRE_DEMO must remain true.")
        if not os.getenv("MT5_BRIDGE_URL", "").strip() or not os.getenv("MT5_BRIDGE_TOKEN", "").strip():
            raise PermissionError("MT5 bridge URL/token are required in the private deployment environment.")
        from .venue.mt5_agent import MT5VenueAgent
        if mt5_adapter is None:
            from exchanges.mt5_bridge_adapter import MT5BridgeAdapter
            mt5_adapter = MT5BridgeAdapter()
        # An injected adapter is not exempt from authenticated demo validation.
        mt5_agent = MT5VenueAgent(mt5_adapter)
        if check_mt5_health:
            status = mt5_agent.health_check()
            if status.get("status") != "healthy" or status.get("account_mode") != "demo":
                raise ConnectionError("MT5 bridge did not pass its authenticated demo health check.")
        venues["mt5"] = mt5_adapter

    if not venues:
        raise PermissionError("No execution venue is enabled in STRATEX_ALLOWED_EXECUTION_VENUES.")
    return StratexOrderManager(venues, authorize_intent, store=store)


def build_reconciliation_agent(
    venues: dict[str, object],
    *,
    store: OrderIntentStore | None = None,
):
    """Construct a read-only reconciler from explicit venue adapters.

    Adapters without an evidence-based reconcile_intent method are omitted;
    their pending intents remain pending instead of being guessed as cancelled.
    """
    from .reconciliation_agent import OrderReconciliationAgent

    resolvers = {}
    for venue_name, adapter in venues.items():
        resolver = getattr(adapter, "reconcile_intent", None)
        if callable(resolver):
            resolvers[str(venue_name).strip().lower()] = resolver
    return OrderReconciliationAgent(store or OrderIntentStore(), resolvers)