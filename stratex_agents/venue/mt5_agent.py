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

    def evaluate_intent_risk(self, intent, context: dict[str, Any] | None = None) -> tuple[bool, str, str]:
        """Use the demo terminal's broker-aware sizing before the OMS approves an MT5 intent."""
        if getattr(intent, "venue", None) != "mt5" or getattr(intent, "environment", None) != "mt5_demo":
            return False, "MT5_DEMO_INTENT_REQUIRED", "Only MT5 demo intents can reach this evaluator."
        if not self.configured:
            return False, "MT5_BRIDGE_NOT_CONFIGURED", "MT5 demo bridge configuration is incomplete."
        try:
            sizing = self.adapter.calculate_position_size(
                intent.symbol, intent.side, intent.stop_loss, intent.take_profit
            )
            requested = float(intent.quantity)
            recommended = float(sizing["recommended_volume"])
            step = float(sizing["volume_step"])
            minimum = float(sizing["volume_min"])
            if requested < minimum:
                return False, "MT5_VOLUME_BELOW_MINIMUM", f"Requested {requested} is below broker minimum {minimum}."
            if step <= 0 or abs(round(requested / step) * step - requested) > 1e-8:
                return False, "MT5_VOLUME_STEP_INVALID", "Requested volume is not aligned to the broker volume step."
            if requested > recommended + 1e-9:
                return False, "MT5_RISK_OR_MARGIN_LIMIT", (
                    f"Requested volume {requested} exceeds broker-calculated safe volume {recommended}."
                )
            details = (
                f"risk={sizing['risk_amount']:.2f} {sizing['account_currency']}; "
                f"risk_budget={sizing['risk_budget']:.2f}; "
                f"margin={sizing['margin_required']:.2f}; "
                f"margin_budget={sizing['margin_budget']:.2f}; "
                f"recommended_volume={recommended}"
            )
            return True, "MT5_BROKER_RISK_OK", details
        except Exception as exc:
            # A missing quote, calculator error, stale terminal or missing contract
            # data is a denial, never a reason to use Binance-style lot arithmetic.
            return False, "MT5_BROKER_RISK_UNAVAILABLE", type(exc).__name__

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
            if account.get("demo") is not True:
                self.adapter.is_connected = False
                return {
                    "agent_id": self.agent_id,
                    "status": "unhealthy",
                    "connected": False,
                    "demo_required": True,
                    "server": account.get("server"),
                    "account_mode": "rejected",
                    "error": "bridge did not confirm a demo account",
                }
            return {
                "agent_id": self.agent_id,
                "status": "healthy",
                "connected": True,
                "demo_required": True,
                "server": account.get("server"),
                # Do not expose login IDs, tokens, account balances or credentials.
                "account_mode": "demo",
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
