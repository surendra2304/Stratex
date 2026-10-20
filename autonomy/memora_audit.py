"""Memora memory feed audit integration for Stratex trade intents and risk events.

Publishes completed trade intents, risk rejections, and emergency halts to the
central Memora memory feed under the 'stratex' agent namespace.
This module is non-blocking and fail-safe: external Memora timeouts or outages
never disrupt local trading or order management.
"""
from __future__ import annotations

import logging
from typing import Any

from autonomy.memora_client import memora_client
from resilience import sanitize_secrets

logger = logging.getLogger("memora_audit")


def _intent_summary(intent: Any) -> dict[str, Any]:
    if isinstance(intent, dict):
        return {
            "client_order_id": intent.get("client_order_id"),
            "venue": intent.get("venue"),
            "symbol": intent.get("symbol"),
            "side": intent.get("side"),
            "order_type": intent.get("order_type"),
            "quantity": intent.get("quantity"),
            "environment": intent.get("environment"),
            "strategy": intent.get("strategy"),
        }
    return {
        "client_order_id": getattr(intent, "client_order_id", None),
        "venue": getattr(intent, "venue", None),
        "symbol": getattr(intent, "symbol", None),
        "side": getattr(intent, "side", None),
        "order_type": getattr(intent, "order_type", None),
        "quantity": getattr(intent, "quantity", None),
        "environment": getattr(intent, "environment", None),
        "strategy": getattr(intent, "strategy", None),
    }


def publish_trade_intent_audit(
    intent: Any,
    status: str,
    *,
    result: dict[str, Any] | None = None,
    error: str | None = None,
    client: Any | None = None,
) -> bool:
    """Publish a completed trade intent to the Memora memory feed."""
    mc = client or memora_client
    summary = _intent_summary(intent)
    client_order_id = summary.get("client_order_id") or "unknown"
    venue = summary.get("venue") or "unknown"
    symbol = summary.get("symbol") or "unknown"
    side = summary.get("side") or "unknown"

    user_text = f"Execute trade intent {client_order_id}: {side} {symbol} on {venue} (status: {status})"
    agent_text = (
        f"Intent {client_order_id} reached terminal state {status} on venue {venue}. "
        f"Result: {sanitize_secrets(result) if result else 'none'}; Error: {error or 'none'}"
    )

    metadata = sanitize_secrets({
        **summary,
        "status": status,
        "result": result,
        "error": error,
    })

    try:
        resp = mc.record_interaction(
            agent_name="stratex",
            user_input=user_text,
            agent_output=agent_text,
            event_type="trade_intent_completed",
            tags=["trade_intent", str(venue).lower(), str(status).lower()],
            metadata=metadata,
        )
        success = isinstance(resp, dict) and resp.get("status") != "error"
        if not success:
            logger.warning("[MEMORA_AUDIT] Failed to publish trade intent %s: %s", client_order_id, resp)
        return success
    except Exception as exc:
        logger.warning("[MEMORA_AUDIT] Exception publishing trade intent %s (%s)", client_order_id, type(exc).__name__)
        return False


def publish_risk_rejection_audit(
    intent: Any,
    reason: str,
    details: str = "",
    *,
    client: Any | None = None,
) -> bool:
    """Publish a deterministic risk rejection to the Memora memory feed."""
    mc = client or memora_client
    summary = _intent_summary(intent)
    client_order_id = summary.get("client_order_id") or "unknown"
    venue = summary.get("venue") or "unknown"
    symbol = summary.get("symbol") or "unknown"

    user_text = f"Risk evaluation for intent {client_order_id}: {symbol} on {venue}"
    agent_text = f"Order blocked by risk gate: reason={reason}; details={details}"

    metadata = sanitize_secrets({
        **summary,
        "risk_rejection_reason": reason,
        "risk_rejection_details": details,
    })

    try:
        resp = mc.record_interaction(
            agent_name="stratex",
            user_input=user_text,
            agent_output=agent_text,
            event_type="risk_rejection",
            tags=["risk_rejection", str(venue).lower()],
            metadata=metadata,
        )
        success = isinstance(resp, dict) and resp.get("status") != "error"
        if not success:
            logger.warning("[MEMORA_AUDIT] Failed to publish risk rejection %s: %s", client_order_id, resp)
        return success
    except Exception as exc:
        logger.warning("[MEMORA_AUDIT] Exception publishing risk rejection %s (%s)", client_order_id, type(exc).__name__)
        return False


def publish_emergency_halt_audit(
    actor: str,
    reason: str,
    orders_cancelled: int = 0,
    *,
    client: Any | None = None,
) -> bool:
    """Publish an emergency halt event to the Memora memory feed."""
    mc = client or memora_client
    user_text = f"Emergency halt triggered by {actor}: {reason}"
    agent_text = f"Atomic emergency halt executed: OMS locked, {orders_cancelled} open orders cancelled."
    metadata = {
        "actor": actor,
        "reason": reason,
        "orders_cancelled": orders_cancelled,
        "oms_state": "LOCKED",
    }
    try:
        resp = mc.record_interaction(
            agent_name="stratex",
            user_input=user_text,
            agent_output=agent_text,
            event_type="emergency_halt",
            tags=["emergency_halt", "oms_locked"],
            metadata=metadata,
        )
        return isinstance(resp, dict) and resp.get("status") != "error"
    except Exception as exc:
        logger.warning("[MEMORA_AUDIT] Exception publishing emergency halt (%s)", type(exc).__name__)
        return False
