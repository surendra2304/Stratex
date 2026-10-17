"""Opt-in, read-only order reconciliation worker for Stratex.

This worker never creates, modifies or cancels orders. It queries the configured
venue adapters and updates the shared durable OMS ledger only when a venue
explicitly confirms a terminal order state. It is disabled unless both the OMS
entry route and reconciliation worker are explicitly enabled.
"""
from __future__ import annotations

import os
import signal
import sys
import threading
import time

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from logger import get_logger
from stratex_agents.order_store import OrderIntentStore
from stratex_agents.reconciliation_agent import OrderReconciliationAgent

logger = get_logger("reconciliation_worker")


def _enabled(name: str) -> bool:
    return os.getenv(name, "false").strip().lower() in {"1", "true", "yes", "on"}


def reconcile_worker_configuration_error() -> str | None:
    """Return a reason to refuse startup; never normalize unsafe flags automatically."""
    if not _enabled("STRATEX_RECONCILIATION_ENABLED"):
        return "STRATEX_RECONCILIATION_ENABLED is not true"
    if not _enabled("STRATEX_ORDER_MANAGER_ENABLED"):
        return "STRATEX_ORDER_MANAGER_ENABLED is not true"
    if _enabled("LIVE_TRADING_ENABLED") or os.getenv("TRADING_MODE", "").strip().upper() == "LIVE":
        return "live-money mode is forbidden"
    if os.getenv("STRATEX_EXECUTION_MODE", "DISABLED").strip().upper() != "DEMO":
        return "STRATEX_EXECUTION_MODE must be DEMO"
    if os.getenv("TRADING_MODE", "PAPER").strip().upper() != "FUTURES":
        return "TRADING_MODE must be FUTURES for this OMS release"
    allowed = {
        item.strip().lower()
        for item in os.getenv("STRATEX_ALLOWED_EXECUTION_VENUES", "binance").split(",")
        if item.strip()
    }
    if not allowed or not allowed.issubset({"binance", "mt5"}):
        return "STRATEX_ALLOWED_EXECUTION_VENUES must contain binance and/or mt5 only"
    if "binance" in allowed and not _enabled("TESTNET_ENABLED"):
        return "TESTNET_ENABLED must be true for demo/testnet reconciliation"
    if "mt5" in allowed:
        if not _enabled("MT5_BRIDGE_ENABLED") or not _enabled("MT5_REQUIRE_DEMO"):
            return "MT5 bridge must be enabled with MT5_REQUIRE_DEMO=true"
        if not os.getenv("MT5_BRIDGE_URL", "").strip() or not os.getenv("MT5_BRIDGE_TOKEN", "").strip():
            return "MT5 bridge URL and token must be configured"
    return None


def _build_resolvers() -> dict[str, object]:
    """Construct the explicitly allowed adapters; never substitutes another venue."""
    allowed = {
        item.strip().lower()
        for item in os.getenv("STRATEX_ALLOWED_EXECUTION_VENUES", "binance").split(",")
        if item.strip()
    }
    resolvers: dict[str, object] = {}

    if "binance" in allowed:
        from stratex_agents.venue.binance_futures_oms_adapter import BinanceFuturesOMSAdapter

        binance = BinanceFuturesOMSAdapter()
        resolvers["binance"] = binance.reconcile_intent

    if "mt5" in allowed:
        from exchanges.mt5_bridge_adapter import MT5BridgeAdapter
        from stratex_agents.venue.mt5_agent import MT5VenueAgent

        mt5 = MT5BridgeAdapter()
        status = MT5VenueAgent(mt5).health_check()
        if status.get("status") != "healthy" or status.get("account_mode") != "demo":
            raise ConnectionError("MT5 did not pass its authenticated demo health check")
        resolvers["mt5"] = mt5.reconcile_intent

    return resolvers


def run_worker(stop_event: threading.Event | None = None) -> int:
    """Run the read-only reconciler until a signal or test stop-event is received."""
    error = reconcile_worker_configuration_error()
    if error:
        logger.warning("[RECONCILIATION] Worker not started: %s", error)
        return 0

    event = stop_event or threading.Event()
    interval = 30
    try:
        interval = max(5, min(300, int(os.getenv("STRATEX_RECONCILIATION_INTERVAL_SECONDS", "30"))))
    except (TypeError, ValueError):
        logger.warning("[RECONCILIATION] Invalid interval; using 30 seconds")

    store = OrderIntentStore()
    logger.info(
        "[RECONCILIATION] Starting read-only worker; interval=%ss; ledger=%s",
        interval, os.getenv("STRATEX_ORDER_DB", "state/stratex_orders.sqlite3"),
    )
    agent = None

    while not event.is_set():
        if agent is None:
            try:
                resolvers = _build_resolvers()
                if not resolvers:
                    raise RuntimeError("no venue resolvers are available")
                agent = OrderReconciliationAgent(store, resolvers)
            except Exception as exc:
                # Retry later if configuration or a private bridge is temporarily unavailable.
                logger.error("[RECONCILIATION] Cannot initialize venue resolvers (%s).", type(exc).__name__)
                event.wait(interval)
                continue

        try:
            result = agent.reconcile_once(limit=100)
            logger.info(
                "[RECONCILIATION] candidates=%s confirmed=%s resolved=%s pending=%s errors=%s",
                result.get("candidates", 0), result.get("confirmed", 0),
                result.get("resolved", 0), result.get("pending", 0), result.get("errors", 0),
            )
        except Exception as exc:
            # Do not log request headers, API secrets, bridge tokens or raw HTTP objects.
            logger.error("[RECONCILIATION] Cycle failed (%s); unresolved intents remain persisted.", type(exc).__name__)
        event.wait(interval)

    logger.info("[RECONCILIATION] Worker stopped.")
    return 0


def main() -> int:
    event = threading.Event()

    def request_stop(_signum, _frame):
        event.set()

    signal.signal(signal.SIGTERM, request_stop)
    signal.signal(signal.SIGINT, request_stop)
    return run_worker(event)


if __name__ == "__main__":
    raise SystemExit(main())
