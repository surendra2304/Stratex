"""Deterministic Stratex risk-authorization agent.

This is a policy wrapper around explicit numeric risk engines, not an LLM agent.
It refuses venues for which no venue-correct risk evaluator was provided. In
particular, Binance-style quantity×price limits are not reused for MT5 CFD lots.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from .order_intent import TradeIntent


@dataclass(frozen=True)
class RiskDecision:
    approved: bool
    venue: str
    client_order_id: str
    reason: str
    details: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "approved": self.approved,
            "venue": self.venue,
            "client_order_id": self.client_order_id,
            "reason": self.reason,
            "details": self.details,
        }


class DeterministicRiskAgent:
    """Apply the existing Stratex RiskGate plus explicit execution policy checks."""

    agent_id = "risk_guard"

    def __init__(
        self,
        *,
        binance_risk_gate: Any | None = None,
        execution_policy: Callable[[], tuple[bool, str]] | None = None,
        mt5_risk_evaluator: Callable[[TradeIntent, dict[str, Any]], tuple[bool, str, str]] | None = None,
    ):
        self.binance_risk_gate = binance_risk_gate
        self.execution_policy = execution_policy
        self.mt5_risk_evaluator = mt5_risk_evaluator

    def authorize(self, intent: TradeIntent, context: dict[str, Any]) -> RiskDecision:
        """Return an explicit allow/deny decision. Missing inputs always mean deny."""
        if not isinstance(context, dict):
            return RiskDecision(False, intent.venue, intent.client_order_id, "RISK_CONTEXT_INVALID")
        if self.execution_policy is None:
            return RiskDecision(False, intent.venue, intent.client_order_id, "EXECUTION_POLICY_UNAVAILABLE")
        try:
            policy_allowed, policy_reason = self.execution_policy()
        except Exception as exc:
            return RiskDecision(
                False, intent.venue, intent.client_order_id,
                "EXECUTION_POLICY_ERROR", type(exc).__name__,
            )
        if not policy_allowed:
            return RiskDecision(
                False, intent.venue, intent.client_order_id,
                "EXECUTION_POLICY_BLOCKED", str(policy_reason),
            )

        if intent.venue == "binance":
            if self.binance_risk_gate is None:
                return RiskDecision(False, intent.venue, intent.client_order_id, "BINANCE_RISK_GATE_UNAVAILABLE")
            required = ("current_equity", "active_positions", "entry_price", "data_health_status")
            missing = [key for key in required if key not in context or context[key] is None]
            if missing:
                return RiskDecision(
                    False, intent.venue, intent.client_order_id, "RISK_CONTEXT_INCOMPLETE",
                    ",".join(missing),
                )
            try:
                allowed, reason, details = self.binance_risk_gate.evaluate_risk(
                    intent.symbol,
                    intent.side,
                    context["current_equity"],
                    context["active_positions"],
                    intent.quantity,
                    context["entry_price"],
                    context["data_health_status"],
                )
            except Exception as exc:
                return RiskDecision(
                    False, intent.venue, intent.client_order_id, "RISK_GATE_ERROR", type(exc).__name__,
                )
            return RiskDecision(bool(allowed), intent.venue, intent.client_order_id, str(reason), str(details or ""))

        if intent.venue == "mt5":
            if self.mt5_risk_evaluator is None:
                return RiskDecision(
                    False, intent.venue, intent.client_order_id,
                    "MT5_INSTRUMENT_RISK_EVALUATOR_REQUIRED",
                    "MT5 needs broker-aware lot, tick-value, margin and account-currency risk calculations.",
                )
            try:
                allowed, reason, details = self.mt5_risk_evaluator(intent, context)
            except Exception as exc:
                return RiskDecision(
                    False, intent.venue, intent.client_order_id, "MT5_RISK_EVALUATOR_ERROR", type(exc).__name__,
                )
            return RiskDecision(bool(allowed), intent.venue, intent.client_order_id, str(reason), str(details or ""))

        return RiskDecision(False, intent.venue, intent.client_order_id, "UNSUPPORTED_RISK_VENUE")

    def health(self) -> dict[str, Any]:
        return {
            "agent_id": self.agent_id,
            "status": "ready" if self.execution_policy is not None else "policy_configuration_required",
            "binance_risk_gate_available": self.binance_risk_gate is not None,
            "mt5_risk_evaluator_available": self.mt5_risk_evaluator is not None,
            "execution_authority": False,
            "can_deny_execution": True,
        }
