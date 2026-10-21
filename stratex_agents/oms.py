"""Shared Stratex order manager: authorization, durable idempotency and explicit venue routing.

This module intentionally has no automatic cross-venue failover. A timed-out order
is UNKNOWN until reconciled and must never be blindly submitted to another broker.
Only demo/testnet environments are accepted here.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Callable, Protocol

from .order_intent import TradeIntent
from .order_store import IntentConflictError, IntentInFlightError, OrderIntentStore


class VenueSubmitter(Protocol):
    def submit_intent(self, intent: TradeIntent) -> dict[str, Any]:
        """Submit one intent to the explicitly selected venue."""


class OrderBlockedError(PermissionError):
    """Raised before venue submission when a required safety condition fails."""


@dataclass(frozen=True)
class OMSResult:
    client_order_id: str
    venue: str
    status: str
    result: dict[str, Any] | None = None
    reason: str | None = None
    duplicate: bool = False


def _truthy(value: str | None) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes", "on"}


def default_kill_switch_check() -> tuple[bool, str]:
    """Read the repository's existing kill-switch helpers; fail closed if unreadable."""
    try:
        from panic_state import is_kill_switch_locked, is_panic_active
        if is_panic_active():
            return True, "panic switch is active"
        if is_kill_switch_locked():
            return True, "kill-switch lock is active"
        return False, ""
    except Exception as exc:
        return True, f"kill-switch state could not be verified ({type(exc).__name__})"


class StratexOrderManager:
    """One stable order lifecycle across venue agents, guarded by an injected risk gate."""

    def __init__(
        self,
        venues: dict[str, VenueSubmitter],
        authorize_intent: Callable[[TradeIntent], tuple[bool, str]],
        *,
        store: OrderIntentStore | None = None,
        kill_switch_check: Callable[[], tuple[bool, str]] = default_kill_switch_check,
    ):
        self.venues = {key.strip().lower(): value for key, value in venues.items()}
        self.authorize_intent = authorize_intent
        self.store = store or OrderIntentStore()
        self.kill_switch_check = kill_switch_check

    def _validate_environment(self, intent: TradeIntent) -> None:
        mode = os.getenv("STRATEX_EXECUTION_MODE", "DISABLED").strip().upper()
        if _truthy(os.getenv("LIVE_TRADING_ENABLED")) or os.getenv("TRADING_MODE", "").strip().upper() == "LIVE":
            raise OrderBlockedError("Live-money execution is disabled by Stratex policy.")
        if mode != "DEMO":
            raise OrderBlockedError(
                f"STRATEX_EXECUTION_MODE={mode}; select DEMO explicitly before demo order submission."
            )
        if intent.venue not in self.venues:
            raise OrderBlockedError(f"Venue {intent.venue!r} is not registered in this runtime.")
        allowed = {
            p.strip().lower()
            for p in os.getenv("STRATEX_ALLOWED_EXECUTION_VENUES", "binance").split(",")
            if p.strip()
        }
        if intent.venue not in allowed:
            raise OrderBlockedError(f"Venue {intent.venue!r} is not in STRATEX_ALLOWED_EXECUTION_VENUES.")
        if intent.venue == "binance":
            trading_mode = os.getenv("TRADING_MODE", "PAPER").strip().upper()
            if trading_mode != "FUTURES":
                raise OrderBlockedError("The Binance OMS adapter in this release supports USDⓈ-M Futures only; TRADING_MODE must be FUTURES.")
            demo = _truthy(os.getenv("BINANCE_DEMO_TRADING"))
            expected = "binance_demo" if demo else "binance_testnet"
            if intent.environment != expected:
                raise OrderBlockedError(
                    f"Intent environment {intent.environment!r} doesn't match selected Binance environment {expected!r}."
                )
            if not _truthy(os.getenv("TESTNET_ENABLED", "true")):
                raise OrderBlockedError("TESTNET_ENABLED is false.")
            if os.getenv("TRADING_MODE", "PAPER").strip().upper() not in {"TESTNET", "FUTURES"}:
                raise OrderBlockedError("The existing Binance strategy runtime must be configured for TESTNET/FUTURES mode.")
        elif intent.venue == "mt5":
            if intent.environment != "mt5_demo":
                raise OrderBlockedError("MT5 demo is the only supported MT5 environment.")
            if not _truthy(os.getenv("MT5_BRIDGE_ENABLED")) or not _truthy(os.getenv("MT5_REQUIRE_DEMO", "true")):
                raise OrderBlockedError("MT5 bridge is disabled or demo-only enforcement is disabled.")
            if not os.getenv("MT5_BRIDGE_URL", "").strip() or not os.getenv("MT5_BRIDGE_TOKEN", "").strip():
                raise OrderBlockedError("MT5 bridge private URL and token must be configured.")

    @staticmethod
    def _status_from_result(result: dict[str, Any]) -> str:
        status = str(result.get("status", "UNKNOWN")).upper()
        return {
            "OPEN": "ACCEPTED", "SUBMITTED": "ACCEPTED", "NEW": "ACCEPTED",
            "PARTIALLY_FILLED": "PARTIALLY_FILLED", "PARTIAL": "PARTIALLY_FILLED",
            "FILLED": "FILLED", "CANCELLED": "CANCELLED",
            "REJECTED": "REJECTED", "UNKNOWN": "UNKNOWN", "PENDING": "ACCEPTED",
        }.get(status, "UNKNOWN")

    def submit(self, intent: TradeIntent) -> OMSResult:
        """Persist intent first, authorize and submit once; never blindly retry timeouts."""
        existing = self.store.reserve(intent.client_order_id, intent.payload_hash(), intent.to_dict())
        if existing["kind"] == "duplicate":
            return OMSResult(
                client_order_id=intent.client_order_id,
                venue=intent.venue,
                status=existing["status"],
                result=existing.get("result"),
                reason=existing.get("error"),
                duplicate=True,
            )
        try:
            self._validate_environment(intent)
            blocked, reason = self.kill_switch_check()
            if blocked:
                raise OrderBlockedError(reason or "Kill switch is active.")
            approved, risk_reason = self.authorize_intent(intent)
            if not approved:
                self.store.transition(intent.client_order_id, "REJECTED", result={"status": "REJECTED"}, error=risk_reason)
                try:
                    from autonomy.memora_audit import publish_risk_rejection_audit
                    publish_risk_rejection_audit(intent, risk_reason)
                except Exception:
                    pass
                try:
                    from notifications.telegram_bot import get_telegram_bot
                    get_telegram_bot().notify_risk_rejection(intent, risk_reason)
                except Exception:
                    pass
                return OMSResult(intent.client_order_id, intent.venue, "REJECTED", reason=risk_reason)
            self.store.transition(intent.client_order_id, "SUBMITTING")
            # Deliberately no automatic failover. Network exceptions are ambiguous.
            result = self.venues[intent.venue].submit_intent(intent)
            if not isinstance(result, dict):
                raise RuntimeError("Venue adapter returned an invalid non-dictionary result.")
            status = self._status_from_result(result)
            self.store.transition(intent.client_order_id, status, result=result)
            if status in {"ACCEPTED", "FILLED", "CANCELLED", "REJECTED"}:
                try:
                    from autonomy.memora_audit import publish_trade_intent_audit
                    publish_trade_intent_audit(intent, status, result=result)
                except Exception:
                    pass
                try:
                    from notifications.telegram_bot import get_telegram_bot
                    get_telegram_bot().notify_trade_intent(intent, status=status)
                except Exception:
                    pass
            return OMSResult(intent.client_order_id, intent.venue, status, result=result)
        except OrderBlockedError as exc:
            self.store.transition(intent.client_order_id, "REJECTED", result={"status": "REJECTED"}, error=str(exc))
            try:
                from autonomy.memora_audit import publish_risk_rejection_audit
                publish_risk_rejection_audit(intent, str(exc))
            except Exception:
                pass
            return OMSResult(intent.client_order_id, intent.venue, "REJECTED", reason=str(exc))
        except (IntentConflictError, IntentInFlightError):
            raise
        except Exception as exc:
            # We cannot generally know whether the venue accepted the request before
            # a network/process error. Mark UNKNOWN to prevent a duplicate submission.
            try:
                self.store.transition(intent.client_order_id, "UNKNOWN", error=type(exc).__name__)
            finally:
                return OMSResult(
                    intent.client_order_id, intent.venue, "UNKNOWN",
                    reason="Submission outcome is ambiguous; reconcile venue state before any retry.",
                )
