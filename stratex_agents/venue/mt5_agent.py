"""MT5 venue agent wrapper around the demo-only bridge adapter.

The wrapper exposes configuration and health state for the orchestration layer.
Order submission remains governed by the unified execution policy and should not
be called directly from strategy or LLM code.
"""
from __future__ import annotations

import os
from typing import Any

from exchanges.mt5_bridge_adapter import MT5BridgeAdapter, MT5BridgeError


class MT5VenueAgent:
    agent_id = "mt5"

    def __init__(self, adapter: MT5BridgeAdapter | None = None):
        self.adapter = adapter or MT5BridgeAdapter()

    @property
    def configured(self) -> bool:
        return bool(
            self.adapter.enabled
            and self.adapter.base_url
            and self.adapter.token
            and os.getenv("MT5_REQUIRE_DEMO", "true").lower() not in {"0", "false", "no"}
        )

    def health_check(self) -> dict[str, Any]:
        """Run an authenticated health check; never report success on missing config."""
        if not self.configured:
            return {
                "agent_id": self.agent_id,
                "status": "disabled" if not self.adapter.enabled else "configuration_required",
                "connected": False,
                "demo_required": True,
            }
        try:
            account = self.adapter.health()
            return {
                "agent_id": self.agent_id,
                "status": "healthy",
                "connected": True,
                "demo_required": True,
                "server": account.get("server"),
                # Do not expose login IDs, tokens, account balances or credentials.
                "account_mode": "demo" if account.get("demo") else "rejected",
            }
        except MT5BridgeError as exc:
            self.adapter.is_connected = False
            return {
                "agent_id": self.agent_id,
                "status": "unhealthy",
                "connected": False,
                "demo_required": True,
                "error": str(exc),
            }
