"""Paper-only Stratex decisions for Futuris advisories over Memora's durable feed.

One journey, one correlation ID: the correlation ID carried by the consumed
futuris.forecast event is threaded into the published stratex.decision
envelope. This module never places any order — live or testnet — it only
records the paper-policy evaluation. Stratex's own gates
(LIVE_FORBIDDEN_BY_DESIGN) remain the single source of truth for execution.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import time
import urllib.error
import urllib.request
from typing import Any, Callable

CONSUMER_ID_DEFAULT = "stratex-journey"
DECISION_INTENT = "stratex.decision"
_MEMORA_BASE_DEFAULT = "https://memora-cavc.onrender.com"

# Memora's ack route answers {"status": "acknowledged"}; some client wrappers
# normalize to "ok". Both mean the durable cursor advanced.
_ACK_OK_STATUSES = {"ok", "acknowledged"}


def _ack_ok(ack: Any) -> bool:
    return isinstance(ack, dict) and ack.get("status") in _ACK_OK_STATUSES


def _decision_identity(forecast_event_id: str) -> tuple[str, str]:
    """Deterministic (decision_id, message_id) so retries deduplicate."""
    digest = hashlib.sha256(forecast_event_id.encode("utf-8")).hexdigest()[:16]
    return f"dec-{digest}", f"stratex-dec-{digest}"


def evaluate_advisory(payload: dict[str, Any], *, execution_policy: Any = None) -> dict[str, Any]:
    """Run Stratex's real execution gates and produce a paper-only decision receipt.

    ``execution_policy`` is injectable for tests; production always uses
    ``execution.ExecutionPolicy``, whose can_place_order() is hard-blocked.
    """
    from deployment.live_authorization import LiveAuthorizationVerifier
    from execution import ExecutionPolicy

    policy = execution_policy or ExecutionPolicy
    allowed, policy_reason = policy.can_place_order()
    verifier = LiveAuthorizationVerifier()
    state = verifier.verify_all_authorizations()
    gates = [str(item)[:200] for item in list(state.blocking_errors)[:12]]

    # This module records intent only. Even when a trading mode would be
    # permitted elsewhere, nothing is placed here; the decision stays a
    # paper-mode observation ("watch"), never an execution claim.
    action = "watch" if allowed else "paper_intent"
    forecast_id = str(payload.get("forecast_id", "")).strip()[:128]
    summary = (
        f"Advisory forecast_id={forecast_id or 'unknown'} "
        f"prediction={payload.get('prediction')} probability={payload.get('probability')} "
        f"evaluated under Stratex paper policy. Execution verdict: {policy_reason}. "
        "No live order was placed and none will be placed by this decision."
    )
    return {
        "decision_id": "",
        "forecast_id": forecast_id,
        "action": action,
        "paper_only": True,
        "policy_reason": str(policy_reason)[:500] or "PAPER_MODE",
        "gates_summary": gates,
        "headline": "Stratex paper-only decision on Futuris advisory",
        "summary": summary[:2000],
        "decided_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }


def build_decision_envelope(
    decision: dict[str, Any],
    *,
    correlation_id: str,
    signing_key: str,
    message_id: str,
) -> dict[str, Any]:
    """Build the HMAC-signed stratex.decision envelope."""
    if not signing_key:
        raise RuntimeError("STRATEX_API_KEY is not configured for signed Memora events")
    payload = {**decision, "decision_id": str(decision.get("decision_id", ""))[:128]}
    envelope: dict[str, Any] = {
        "message_id": message_id,
        "correlation_id": correlation_id,
        "from_agent": "stratex",
        "to_agent": "all",
        "intent": DECISION_INTENT,
        "priority": "normal",
        "ttl": 86400,
        "auth_token": None,
        "payload": payload,
        "created_at": time.time(),
    }
    raw = json.dumps(envelope, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    envelope["signature"] = hmac.new(signing_key.encode("utf-8"), raw, hashlib.sha256).hexdigest()
    return envelope


def publish_decision(
    envelope: dict[str, Any],
    *,
    base_url: str | None = None,
    api_key: str | None = None,
    timeout: float = 10.0,
    post: Callable[[str, dict[str, Any], str], dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """POST the signed envelope to Memora's mesh ingest; explicit receipts only."""
    base = (base_url or os.getenv("MEMORA_URL", _MEMORA_BASE_DEFAULT)).rstrip("/")
    key = api_key or os.getenv("STRATEX_API_KEY", "")
    if post is not None:
        return post(base, envelope, key)
    request = urllib.request.Request(
        f"{base}/mesh/envelope",
        data=json.dumps(envelope).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            status_code = response.status
            body = json.loads(response.read().decode("utf-8") or "{}")
    except urllib.error.HTTPError as exc:
        return {"status": "failed_upstream", "status_code": exc.code}
    except Exception as exc:  # transport-level failure; nothing was accepted
        return {"status": "error", "error": type(exc).__name__}
    if status_code != 202:
        return {"status": "failed_upstream", "status_code": status_code}
    return {
        "status": body.get("status", "accepted"),
        "event_id": body.get("event_id"),
        "cursor": body.get("cursor"),
    }


def consume_and_decide(
    feed_client: Any,
    *,
    post: Callable[[str, dict[str, Any], str], dict[str, Any]] | None = None,
    limit: int = 50,
    consumer_id: str = CONSUMER_ID_DEFAULT,
) -> dict[str, Any]:
    """Drain Stratex's durable cursor; decide paper-only on Futuris advisories.

    Events are acknowledged strictly in order and only after their decision
    receipt was durably accepted by Memora (or the event was irrelevant).
    """
    cursor = feed_client.read_event_cursor("stratex", consumer_id)
    if not isinstance(cursor, dict) or cursor.get("status") != "ok":
        error = cursor.get("error", "invalid cursor response") if isinstance(cursor, dict) else "invalid cursor response"
        return {"status": "error", "stage": "cursor", "error": error}

    page = feed_client.poll_events("stratex", cursor["after_id"], limit)
    if not isinstance(page, dict) or page.get("status") != "ok" or not isinstance(page.get("events"), list):
        error = page.get("error", "invalid event page") if isinstance(page, dict) else "invalid event page"
        return {"status": "error", "stage": "poll", "error": error}

    decisions: list[dict[str, Any]] = []
    for event in page["events"]:
        if not isinstance(event, dict) or not isinstance(event.get("id"), int):
            return {"status": "error", "stage": "event", "acknowledged": len(decisions)}
        payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}

        if event.get("event_type") != "futuris.forecast":
            ack = feed_client.acknowledge_event("stratex", event["id"], consumer_id)
            if not _ack_ok(ack):
                return {"status": "error", "stage": "ack", "event_id": event["id"], "acknowledged": len(decisions)}
            continue

        correlation_id = str(payload.get("correlation_id") or "").strip()[:128]
        decision = evaluate_advisory(payload)
        decision_id, message_id = _decision_identity(str(event.get("event_id", event["id"])))
        decision["decision_id"] = decision_id
        envelope = build_decision_envelope(
            decision,
            correlation_id=correlation_id,
            signing_key=os.getenv("STRATEX_API_KEY", ""),
            message_id=message_id,
        )
        receipt = publish_decision(envelope, post=post)
        if receipt.get("status") not in {"accepted", "duplicate"}:
            return {
                "status": "error",
                "stage": "publish",
                "decision_id": decision_id,
                "receipt": receipt,
                "acknowledged": len(decisions),
            }
        ack = feed_client.acknowledge_event("stratex", event["id"], consumer_id)
        if not _ack_ok(ack):
            return {"status": "error", "stage": "ack", "decision_id": decision_id, "acknowledged": len(decisions)}
        decisions.append(
            {
                "event_id": event["id"],
                "event_id_string": str(event.get("event_id", event["id"])),
                "decision_id": decision_id,
                "message_id": message_id,
                "correlation_id": correlation_id,
                "action": decision["action"],
                "policy_reason": decision["policy_reason"],
                "publish_status": receipt.get("status"),
            }
        )
    return {
        "status": "ok",
        "consumer_id": consumer_id,
        "decisions": decisions,
        "next_after_id": page.get("next_after_id", cursor["after_id"]),
    }
