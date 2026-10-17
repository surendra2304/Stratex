"""Observable agent registry for Stratex's multi-venue architecture.

These are bounded engineering agents/modules, not unconstrained LLMs. Strategy and
intelligence components can propose signals; only deterministic risk/authorization
components may permit execution.
"""
from __future__ import annotations

import os
from typing import Any


def _on(name: str, default: str = "false") -> bool:
    return os.getenv(name, default).strip().lower() in {"1", "true", "yes", "on"}


def _configured_secret(name: str) -> bool:
    value = os.getenv(name, "").strip()
    return bool(value and not value.upper().startswith("YOUR_") and value.upper() not in {"CHANGEME", "REPLACE_ME"})


def get_agent_catalog() -> list[dict[str, Any]]:
    demo_binance = _on("BINANCE_DEMO_TRADING")
    has_demo_credentials = _configured_secret("BINANCE_API_KEY") and _configured_secret("BINANCE_API_SECRET")
    mt5_enabled = _on("MT5_BRIDGE_ENABLED")
    has_mt5_config = bool(os.getenv("MT5_BRIDGE_URL", "").strip()) and _configured_secret("MT5_BRIDGE_TOKEN")
    inference_on = _on("AI_UNIVERSE_ENABLED", "true")
    futuris_on = _on("FUTURIS_FORECAST_ENABLED")
    intelx_on = _on("INTELX_INTELLIGENCE_ENABLED")
    trading_mode = os.getenv("TRADING_MODE", "PAPER").upper()
    safe_mode = _on("PAPER_SAFE_MODE", "true") or not _on("LIVE_TRADING_ENABLED")

    agents = [
        {
            "id": "stratex_core",
            "name": "Stratex Core Orchestrator",
            "kind": "orchestrator",
            "status": "ready",
            "execution_authority": False,
            "responsibility": "Coordinates strategy signals, venue selection, deterministic risk gates and telemetry.",
        },
        {
            "id": "market_data",
            "name": "Market Data Agent",
            "kind": "data",
            "status": "ready",
            "execution_authority": False,
            "responsibility": "Normalizes prices, candles, symbols, contract specifications and freshness metadata.",
        },
        {
            "id": "strategy_supervisor",
            "name": "Strategy Supervisor Agent",
            "kind": "strategy",
            "status": "ready",
            "execution_authority": False,
            "responsibility": "Loads existing Stratex strategy registry; unvalidated strategies remain non-executable.",
        },
        {
            "id": "risk_guard",
            "name": "Risk & Authorization Agent",
            "kind": "risk",
            "status": "ready",
            "execution_authority": False,
            "responsibility": "Enforces deterministic pre-trade limits, environment isolation, authorization and kill-switch policy.",
        },
        {
            "id": "binance_futures",
            "name": "Binance Futures Venue Agent",
            "kind": "venue",
            "status": (
                "configured_demo" if demo_binance and has_demo_credentials else
                "demo_credentials_required" if demo_binance else
                "legacy_testnet_mode" if trading_mode in {"TESTNET", "FUTURES"} else "disabled"
            ),
            "environment": "binance_demo_trading" if demo_binance else "legacy_testnet",
            "execution_authority": False,
            "responsibility": "Owns Binance client, demo/testnet endpoint selection, venue metadata and order API lifecycle.",
        },
        {
            "id": "mt5",
            "name": "IC Markets MT5 Venue Agent",
            "kind": "venue",
            "status": "configured_pending_health_check" if mt5_enabled and has_mt5_config else (
                "missing_bridge_configuration" if mt5_enabled else "disabled"
            ),
            "environment": "demo_required",
            "execution_authority": False,
            "responsibility": "Connects through the private Windows MT5 bridge; preserves ticket identity in hedge mode.",
        },
        {
            "id": "portfolio",
            "name": "Portfolio & Exposure Agent",
            "kind": "portfolio",
            "status": "available",
            "execution_authority": False,
            "responsibility": "Aggregates per-venue balances and positions while maintaining account-specific risk boundaries.",
        },
        {
            "id": "order_management",
            "name": "Durable Order Management Agent",
            "kind": "execution_control",
            "status": "foundation_available_runtime_integration_pending",
            "execution_authority": False,
            "responsibility": "Persists trade intents, enforces idempotency and refuses blind retries of ambiguous submissions.",
        },
        {
            "id": "reconciliation",
            "name": "Order Reconciliation Agent",
            "kind": "operations",
            "status": "foundation_available_runtime_integration_pending",
            "execution_authority": False,
            "responsibility": "Reconciles intents, acknowledgements, fills, pending orders, positions and ledgers after errors/restarts.",
        },
        {
            "id": "inference_advisor",
            "name": "Inference Advisory Agent",
            "kind": "intelligence",
            "status": "advisory_enabled" if inference_on else "disabled",
            "execution_authority": False,
            "responsibility": "May summarize context or rank candidate signals; cannot place orders or override deterministic gates.",
        },
        {
            "id": "futuris_forecast",
            "name": "Futuris Forecast Agent",
            "kind": "intelligence",
            "status": "advisory_enabled" if futuris_on else "disabled",
            "execution_authority": False,
            "responsibility": "Provides calibrated regime/forecast features as advisory input only.",
        },
        {
            "id": "intelx_research",
            "name": "IntelX Research Agent",
            "kind": "intelligence",
            "status": "advisory_enabled" if intelx_on else "disabled",
            "execution_authority": False,
            "responsibility": "Supplies sourced event/news context; it does not authorize orders.",
        },
        {
            "id": "audit_monitor",
            "name": "Audit & Monitoring Agent",
            "kind": "operations",
            "status": "available",
            "execution_authority": False,
            "responsibility": "Surfaces health, mode, order lifecycle events, rejections and audit records.",
        },
    ]
    # Safe-mode information is diagnostic only; ExecutionPolicy remains the authority.
    for agent in agents:
        agent["trading_mode"] = trading_mode
        agent["live_trading_enabled"] = False
        agent["safe_mode_configured"] = safe_mode
    return agents
