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
    ALLOWED_PROGRESSIONS = {
        "RESERVED": {"RESERVED", "SUBMITTING", "ACCEPTED", "PARTIALLY_FILLED", "FILLED", "CANCELLED", "REJECTED", "UNKNOWN"},
        "SUBMITTING": {"SUBMITTING", "ACCEPTED", "PARTIALLY_FILLED", "FILLED", "CANCELLED", "REJECTED", "UNKNOWN"},
        "UNKNOWN": {"UNKNOWN", "ACCEPTED", "PARTIALLY_FILLED", "FILLED", "CANCELLED", "REJECTED"},
        "ACCEPTED": {"ACCEPTED", "PARTIALLY_FILLED", "FILLED", "CANCELLED", "REJECTED"},
        "PARTIALLY_FILLED": {"PARTIALLY_FILLED", "FILLED", "CANCELLED"},
    }

    def __init__(
        self,
        store: OrderIntentStore,
        resolvers: dict[str, Callable[[dict[str, Any]], dict[str, Any]]],
        *,
        portfolio_state_store: Any | None = None,
    ):
        self.store = store
        self.resolvers = {key.strip().lower(): value for key, value in resolvers.items()}
        self.portfolio_state_store = portfolio_state_store

    def reconcile_once(self, limit: int = 100) -> dict[str, Any]:
        """Query pending orders and update the durable ledger only on confirmed venue evidence."""
        candidates = self.store.list_reconcilable(limit=limit)
        records: list[dict[str, Any]] = []
        resolved = 0
        confirmed = 0
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

            previous_status = str(candidate["status"]).upper()
            allowed_next = self.ALLOWED_PROGRESSIONS.get(previous_status, set())
            if status not in allowed_next:
                pending += 1
                records.append({
                    "client_order_id": client_order_id,
                    "venue": venue,
                    "previous_status": previous_status,
                    "state": "pending",
                    "reason": "venue_status_would_regress_order_lifecycle",
                    "venue_status": status,
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
                    if status in {"FILLED", "CANCELLED", "REJECTED"}:
                        try:
                            from autonomy.memora_audit import publish_trade_intent_audit
                            publish_trade_intent_audit(stored.get("payload") or stored, status, result=result)
                        except Exception:
                            pass
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
            confirmed += 1
            if status in {"ACCEPTED", "PARTIALLY_FILLED"}:
                pending += 1
                record_state = "pending"
                record_reason = "venue_confirms_order_is_still_open_or_partially_filled"
            else:
                resolved += 1
                record_state = "resolved"
                record_reason = None
            records.append({
                "client_order_id": client_order_id,
                "venue": venue,
                "previous_status": candidate["status"],
                "status": status,
                "state": record_state,
                "reason": record_reason,
                "evidence": str(response.get("evidence", "venue_confirmation"))[:200],
            })

        return {
            "agent_id": self.agent_id,
            "execution_authority": False,
            "candidates": len(candidates),
            "confirmed": confirmed,
            "resolved": resolved,
            "pending": pending,
            "errors": errors,
            "records": records,
        }

    def reconcile_portfolio_equity(
        self,
        portfolio_agent: Any,
        *,
        account_key: str = "stratex-demo",
        reporting_currency: str = "USD",
    ) -> dict[str, Any]:
        """Snapshot multi-venue equity and persist the UTC-day baseline to portfolio_equity_state."""
        if portfolio_agent is None:
            return {"confirmed": False, "reason": "portfolio_agent_unavailable"}
        store = self.portfolio_state_store
        if store is None:
            from .portfolio_risk_state import PortfolioRiskStateStore
            store = PortfolioRiskStateStore()
            self.portfolio_state_store = store
        try:
            snapshot = portfolio_agent.snapshot(reporting_currency=reporting_currency)
        except Exception as exc:
            return {"confirmed": False, "reason": f"snapshot_error:{type(exc).__name__}"}

        if not isinstance(snapshot, dict) or snapshot.get("status") != "healthy":
            return {"confirmed": False, "reason": "portfolio_degraded", "snapshot": snapshot}
        if snapshot.get("currency_conversion_complete") is not True:
            return {"confirmed": False, "reason": "currency_conversion_incomplete"}
        if snapshot.get("equity_basis") != "account_equity":
            return {"confirmed": False, "reason": "account_equity_not_verified"}

        try:
            enriched = store.enrich_snapshot(snapshot, account_key=account_key)
            return {
                "confirmed": True,
                "account_key": account_key,
                "reporting_currency": reporting_currency,
                "reporting_equity": enriched.get("reporting_equity"),
                "day_start_equity": enriched.get("day_start_equity"),
                "high_water_equity": enriched.get("high_water_equity"),
                "captured_at": enriched.get("captured_at"),
                "utc_day": enriched.get("utc_day"),
            }
        except Exception as exc:
            return {"confirmed": False, "reason": f"persistence_error:{type(exc).__name__}"}

    def health(self) -> dict[str, Any]:
        return {
            "agent_id": self.agent_id,
            "status": "ready" if self.store else "store_unavailable",
            "venue_resolvers": sorted(self.resolvers),
            "execution_authority": False,
        }
