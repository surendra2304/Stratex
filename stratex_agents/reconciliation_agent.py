"""Read-only OMS reconciliation agent.

It only resolves an order when a venue adapter returns explicit confirmation. A
missing order lookup, timeout, flat position, or LLM opinion is not sufficient
evidence to mark an intent cancelled or filled.
"""
from __future__ import annotations

from typing import Any, Callable

from .order_store import OrderIntentStore


class OrderReconciliationAgent:
    agent_id = "reconciliation"
    ACCEPTED_STATUSES = {"ACCEPTED", "PARTIALLY_FILLED", "FILLED", "CANCELLED", "REJECTED"}
    STATUS_ALIASES = {
        "NEW": "ACCEPTED",
        "OPEN": "ACCEPTED",
        "PENDING": "ACCEPTED",
        "SUBMITTED": "ACCEPTED",
        "PARTIAL": "PARTIALLY_FILLED",
        "CANCELED": "CANCELLED",
        "EXPIRED": "CANCELLED",
    }

    def __init__(
        self,
        store: OrderIntentStore,
        resolvers: dict[str, Callable[[dict[str, Any]], dict[str, Any]]],
    ):
        self.store = store
        self.resolvers = {key.strip().lower(): value for key, value in resolvers.items()}

    def reconcile_once(self, limit: int = 100) -> dict[str, Any]:
        """Query pending orders and update the durable ledger only on confirmed venue evidence."""
        candidates = self.store.list_reconcilable(limit=limit)
        records: list[dict[str, Any]] = []
        resolved = 0
        pending = 0
        errors = 0

        for candidate in candidates:
            client_order_id = candidate["client_order_id"]
            stored = self.store.get(client_order_id)
            if not stored:
                errors += 1
                records.append({
                    "client_order_id": client_order_id,
                    "state": "error",
                    "reason": "intent_not_found_in_store",
                })
                continue

            payload = stored.get("payload") or {}
            venue = str(payload.get("venue", "")).strip().lower()
            resolver = self.resolvers.get(venue)
            if resolver is None:
                pending += 1
                records.append({
                    "client_order_id": client_order_id,
                    "venue": venue or None,
                    "previous_status": candidate["status"],
                    "state": "pending",
                    "reason": "venue_resolver_unavailable",
                })
                continue

            try:
                response = resolver(stored)
            except Exception as exc:
                errors += 1
                records.append({
                    "client_order_id": client_order_id,
                    "venue": venue,
                    "previous_status": candidate["status"],
                    "state": "error",
                    "reason": type(exc).__name__,
                })
                continue

            if not isinstance(response, dict) or response.get("confirmed") is not True:
                pending += 1
                records.append({
                    "client_order_id": client_order_id,
                    "venue": venue,
                    "previous_status": candidate["status"],
                    "state": "pending",
                    "reason": "venue_did_not_confirm_order_state",
                })
                continue

            raw_status = str(response.get("status", "")).strip().upper()
            status = self.STATUS_ALIASES.get(raw_status, raw_status)
            if status not in self.ACCEPTED_STATUSES:
                pending += 1
                records.append({
                    "client_order_id": client_order_id,
                    "venue": venue,
                    "previous_status": candidate["status"],
                    "state": "pending",
                    "reason": "unsupported_or_nonfinal_venue_status",
                    "venue_status": raw_status or None,
                })
                continue

            result = response.get("result")
            if result is not None and not isinstance(result, dict):
                errors += 1
                records.append({
                    "client_order_id": client_order_id,
                    "venue": venue,
                    "previous_status": candidate["status"],
                    "state": "error",
                    "reason": "venue_result_invalid",
                })
                continue

            if status != candidate["status"] or result:
                try:
                    self.store.transition(client_order_id, status, result=result)
                except Exception as exc:
                    errors += 1
                    records.append({
                        "client_order_id": client_order_id,
                        "venue": venue,
                        "previous_status": candidate["status"],
                        "state": "error",
                        "reason": type(exc).__name__,
                    })
                    continue
            resolved += 1
            records.append({
                "client_order_id": client_order_id,
                "venue": venue,
                "previous_status": candidate["status"],
                "status": status,
                "state": "resolved",
                "evidence": str(response.get("evidence", "venue_confirmation"))[:200],
            })

        return {
            "agent_id": self.agent_id,
            "execution_authority": False,
            "candidates": len(candidates),
            "resolved": resolved,
            "pending": pending,
            "errors": errors,
            "records": records,
        }

    def health(self) -> dict[str, Any]:
        return {
            "agent_id": self.agent_id,
            "status": "ready" if self.store else "store_unavailable",
            "venue_resolvers": sorted(self.resolvers),
            "execution_authority": False,
        }
