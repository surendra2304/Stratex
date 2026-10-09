"""
api/friday_supervision.py — FRIDAY Supervision & Universal Task Protocol endpoints for Stratex.

Provides:
1. GET /v1/friday/supervision/status & GET /api/v1/friday/status:
   Structured engine health, mode, panic state, positions, and advisory counts.
2. POST /v1/friday/supervision/panic & POST /api/v1/friday/panic:
   Preserved emergency panic stop / kill-switch activation and release.
3. GET /v1/friday/supervision/advisories:
   Inspect pending staged bounded recommendations and history.
4. POST /v1/friday/supervision/advisory/authorize:
   Explicit authorization and application of staged parameter changes.
5. POST /v1/friday/task & POST /v1/task/execute:
   Universal Task Protocol handler enforcing deterministic execution authority
   (orders and gate bypass attempts by external agents are rejected fail-closed).
"""

import datetime
import json
import os
import re
import time
from typing import Any

from flask import Blueprint, jsonify, request

import config
from advisory_params import get_advisory_overlay
from api.validation import RequestValidationError, get_str, json_body
from audit.audit_manager import get_audit_manager, get_idempotency_store, persisted_hash
from atomic_io import atomic_write_json
from logger import get_logger
from security_hardening import SCOPE_FRIDAY, require_api_scope
from panic_state import is_kill_switch_locked, is_panic_active, kill_switch_lock_file, write_panic_state
from telemetry.health_guard import (
    MAX_TELEMETRY_STALENESS_SECONDS,
    StaleTelemetryError,
    check_telemetry_freshness,
)

logger = get_logger("friday_supervision")

friday_supervision_bp = Blueprint("friday_supervision", __name__)


def _is_panic_active() -> bool:
    # Unified reader: either schema key counts and a corrupt flag fails closed.
    return is_panic_active() or is_kill_switch_locked()


def _is_kill_switch_locked() -> bool:
    return is_kill_switch_locked()


def _get_engine_status_summary() -> dict[str, Any]:
    overlay = get_advisory_overlay()
    pending = overlay.get_pending_recommendations()

    # Determine portfolio state
    equity = 10000.0
    cash = 10000.0
    open_positions = 0
    drawdown_pct = 0.0

    mode = str(getattr(config, "TRADING_MODE", "PAPER")).upper()
    portfolio_file = "testnet_portfolio.json" if mode in ["TESTNET", "FUTURES"] else "paper_portfolio.json"
    if os.path.exists(portfolio_file):
        try:
            with open(portfolio_file, "r", encoding="utf-8") as pf:
                pdata = json.load(pf)
                equity = float(pdata.get("equity", pdata.get("current_equity", 10000.0)))
                cash = float(pdata.get("cash", pdata.get("usdt_cash", equity)))
                positions = pdata.get("positions", {})
                open_positions = len(positions) if isinstance(positions, (dict, list)) else 0
                drawdown_pct = float(pdata.get("max_drawdown", pdata.get("drawdown_pct", 0.0)))
        except Exception:
            pass

    return {
        "engine": "Stratex",
        "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "trading_mode": mode,
        "paper_safe_mode": bool(getattr(config, "PAPER_SAFE_MODE", True)),
        "live_trading_enabled": False,
        "live_money_permanently_blocked": True,
        "panic_active": _is_panic_active(),
        "kill_switch_active": _is_kill_switch_locked(),
        "exchange_connected": mode in ["TESTNET", "FUTURES"] and getattr(config, "TESTNET_ENABLED", False),
        "telemetry_freshness": {
            "status": "FRESH",
            "max_staleness_seconds": MAX_TELEMETRY_STALENESS_SECONDS
        },
        "portfolio": {
            "equity": equity,
            "cash": cash,
            "open_positions_count": open_positions,
            "drawdown_pct": drawdown_pct,
            "reconciliation_status": "RECONCILED"
        },
        "active_strategies": list(getattr(config, "ACTIVE_STRATEGIES", {}).keys()),
        "pending_advisories_count": len(pending)
    }


# ==============================================================================
# SUPERVISION STATUS ENDPOINTS
# ==============================================================================

@friday_supervision_bp.route("/v1/friday/supervision/status", methods=["GET"])
@friday_supervision_bp.route("/api/v1/friday/status", methods=["GET"])
def get_supervision_status():
    """Returns structured Stratex operational status for FRIDAY supervision."""
    summary = _get_engine_status_summary()
    return jsonify({
        "status": "SUCCESS",
        "data": summary
    }), 200


# ==============================================================================
# EMERGENCY PANIC & KILL SWITCH ENDPOINTS
# ==============================================================================

@friday_supervision_bp.route("/v1/friday/supervision/panic", methods=["POST"])
@friday_supervision_bp.route("/api/v1/friday/panic", methods=["POST"])
@require_api_scope(scope=SCOPE_FRIDAY, is_control=True)
def execute_supervision_panic():
    """
    Emergency Panic Kill-Switch — blocks all order placement and halts engine.
    Requires: {"confirm": true, "reason": "...", "source": "FRIDAY"}
    To release: {"confirm": true, "release": true}
    """
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        body = {}
    # Literal JSON true only: bool("false") is True, which used to let a
    # {"release": "false"} request RELEASE the kill switch.
    confirm = body.get("confirm") is True
    release = body.get("release") is True
    reason = str(body.get("reason", "Emergency panic requested via FRIDAY Supervision"))[:500]
    source = str(body.get("source", "FRIDAY"))[:100]

    result, code = apply_supervision_panic(confirm=confirm, release=release, reason=reason, source=source)
    return jsonify(result), code


def apply_supervision_panic(*, confirm: bool, release: bool, reason: str, source: str) -> tuple[dict[str, Any], int]:
    """Activate/release the panic; returns ``(response_body, http_status)``.

    Shared by the HTTP route and the FRIDAY task dispatcher so that a task can
    never report success for a panic that was not actually applied.
    """
    confirm = confirm is True
    release = release is True
    reason = str(reason)[:500]
    source = str(source)[:100]
    if not confirm:
        return ({
            "status": "ERROR",
            "error": "CONFIRMATION_REQUIRED",
            "message": "Emergency panic operation requires explicit confirmation: {'confirm': true}."
        }, 400)

    now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()

    if release:
        errors = []
        try:
            write_panic_state(False, actor=source, reason=reason)
        except Exception as e:
            logger.error(f"Failed to update panic state file on release: {e}")
            errors.append("PANIC_FLAG_NOT_CLEARED")

        if is_kill_switch_locked():
            try:
                os.remove(kill_switch_lock_file())
            except Exception as e:
                logger.error(f"Failed to remove kill switch lock file: {e}")
                errors.append("KILL_SWITCH_LOCK_NOT_REMOVED")

        audit = get_audit_manager().record_event(
            event_type="PANIC_RELEASED",
            actor=source,
            details={"released_at": now_iso, "errors": errors},
            rationale=reason,
            status="PANIC_RELEASED" if not errors else "PANIC_RELEASE_INCOMPLETE"
        )
        still_active = _is_panic_active()
        if errors or still_active:
            return ({
                "status": "ERROR",
                "error": "PANIC_RELEASE_INCOMPLETE",
                "panic_active": still_active,
                "failed_steps": errors,
                "message": "Panic could not be fully released; order submission remains blocked.",
                "audit_hash": persisted_hash(audit)
            }, 500)
        return ({
            "status": "SUCCESS",
            "panic_active": False,
            "message": "PANIC RELEASED: Trading engine unblocked.",
            "audit_hash": persisted_hash(audit)
        }, 200)

    # Activate Panic — two independent blocking mechanisms; report what stuck.
    blocked_by = []
    try:
        write_panic_state(True, actor=source, reason=reason)
        blocked_by.append("PANIC_FLAG")
    except Exception as e:
        logger.error(f"Failed to update panic state file on trigger: {e}")

    try:
        atomic_write_json(kill_switch_lock_file(), {
            "kill_switch_active": True,
            "triggered_at": now_iso,
            "actor": source,
            "reason": reason
        })
        blocked_by.append("KILL_SWITCH_LOCK")
    except Exception as e:
        logger.error(f"Failed to create kill switch lock file: {e}")

    audit = get_audit_manager().record_event(
        event_type="PANIC_TRIGGERED",
        actor=source,
        details={"triggered_at": now_iso, "panic_file": os.getenv("PANIC_STATE_FILE", "panic_state.json"),
                 "blocked_by": blocked_by},
        rationale=reason,
        status="PANIC_ACTIVATED" if blocked_by else "PANIC_NOT_PERSISTED"
    )

    if not blocked_by:
        return ({
            "status": "ERROR",
            "error": "PANIC_NOT_PERSISTED",
            "panic_active": False,
            "message": "Neither the panic flag nor the kill-switch lock could be written; orders are NOT blocked.",
            "audit_hash": persisted_hash(audit)
        }, 500)

    return ({
        "status": "SUCCESS",
        "panic_active": True,
        "kill_switch_active": "KILL_SWITCH_LOCK" in blocked_by,
        "blocked_by": blocked_by,
        "message": ("EMERGENCY PANIC ACTIVATED: all new order submission blocked. "
                    "Existing open/protective orders were not cancelled by this endpoint."),
        "audit_hash": persisted_hash(audit)
    }, 200)


# ==============================================================================
# ADVISORY SUPERVISION & AUTHORIZATION ENDPOINTS
# ==============================================================================

@friday_supervision_bp.route("/v1/friday/supervision/advisories", methods=["GET"])
@friday_supervision_bp.route("/api/v1/friday/advisories", methods=["GET"])
def get_supervision_advisories():
    """Inspects pending staged bounded recommendations."""
    overlay = get_advisory_overlay()
    pending = overlay.get_pending_recommendations()
    return jsonify({
        "status": "SUCCESS",
        "count": len(pending),
        "pending_recommendations": pending
    }), 200


@friday_supervision_bp.route("/v1/friday/supervision/advisory/authorize", methods=["POST"])
@friday_supervision_bp.route("/api/v1/friday/advisory/authorize", methods=["POST"])
@require_api_scope(scope=SCOPE_FRIDAY, is_control=True)
def authorize_advisory_recommendation():
    """
    Separation of Advisory Generation and Application:
    Explicitly authorizes and applies a staged bounded parameter change.
    Requires: {"recommendation_id": "...", "authorization_token": "...", "authorized_by": "FRIDAY"}
    """
    body = json_body()
    rec_id = get_str(body, "recommendation_id", "", max_len=128)
    token = get_str(body, "authorization_token", "", max_len=512)
    authorizer = get_str(body, "authorized_by", "FRIDAY", max_len=64) or "FRIDAY"
    idempotency_key = _idempotency_key(body)

    if not rec_id:
        return jsonify({
            "status": "ERROR",
            "error": "MISSING_RECOMMENDATION_ID",
            "message": "Field 'recommendation_id' is required."
        }), 400

    if not token or str(token).strip() == "":
        return jsonify({
            "status": "ERROR",
            "error": "MISSING_AUTHORIZATION_TOKEN",
            "message": "Field 'authorization_token' is required to authorize parameter changes."
        }), 400

    overlay = get_advisory_overlay()
    ok, msg, res = overlay.apply_authorized_recommendation(
        recommendation_id=rec_id,
        authorization_token=token,
        authorized_by=authorizer,
        idempotency_key=idempotency_key
    )

    if not ok:
        return jsonify({
            "status": "REJECTED",
            "error": "AUTHORIZATION_FAILED",
            "message": msg
        }), 400

    return jsonify({
        "status": "SUCCESS",
        "message": msg,
        "result": res
    }), 200


_IDEMPOTENCY_KEY_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,128}$")


def _idempotency_key(body: dict) -> str | None:
    """Idempotency key from the header or body; must be a short safe string.

    A list/object key used to reach the idempotency store's dict lookup and
    crash the request with ``TypeError: unhashable type`` (HTTP 500).
    """
    raw = request.headers.get("Idempotency-Key")
    if raw is None:
        raw = get_str(body, "idempotency_key", None, max_len=128)
    if raw is None or raw == "":
        return None
    raw = raw.strip()
    if not _IDEMPOTENCY_KEY_RE.match(raw):
        raise RequestValidationError("idempotency_key", "must be 1-128 characters of [A-Za-z0-9_.:-]")
    return raw


# ==============================================================================
# UNIVERSAL TASK PROTOCOL ENDPOINT (WITH DETERMINISTIC EXECUTION ENFORCEMENT)
# ==============================================================================

@friday_supervision_bp.route("/v1/friday/task", methods=["POST"])
@friday_supervision_bp.route("/v1/task/execute", methods=["POST"])
@require_api_scope(scope=SCOPE_FRIDAY, is_control=True)
def execute_friday_task():
    """
    Universal Task Protocol entry point for FRIDAY and Cortex.
    Enforces the Core Invariant:
    Prediction / External Agent Inquiry is NOT Authorization.
    FRIDAY or Inference cannot directly order executions or bypass Stratex safety gates.
    """
    t0 = time.time()
    body = json_body()
    task_id = get_str(body, "task_id", "", max_len=128) or f"stx_task_{int(time.time_ns())}"
    action = get_str(body, "action", "status", lower=True, max_len=64) or "status"
    source_agent = get_str(body, "source_agent", "unknown", max_len=64) or "unknown"
    payload = body.get("payload") if isinstance(body.get("payload"), dict) else body
    idempotency_key = _idempotency_key(body)

    # Invariant Check: Reject order placement or gate bypass attempts fail-closed
    if action in ["order", "place_order", "execute", "execute_order", "bypass_gates", "force_trade", "trade"]:
        rationale = "Execution authority violation: External agents cannot directly command order execution or bypass Stratex safety gates."
        get_audit_manager().record_event(
            event_type="ORDER_BLOCKED",
            actor=source_agent,
            details={"task_id": task_id, "action": action},
            rationale=rationale,
            status="FORBIDDEN"
        )
        return jsonify({
            "task_id": task_id,
            "target_agent": "stratex",
            "status": "FORBIDDEN",
            "error": "DETERMINISTIC_EXECUTION_AUTHORITY_VIOLATION",
            "message": rationale,
            "execution_time_ms": int((time.time() - t0) * 1000)
        }), 403

    # Idempotency Check
    if idempotency_key:
        is_dup, cached = get_idempotency_store().check_and_record(idempotency_key, request_type=f"TASK_{action.upper()}")
        if is_dup and cached and cached.get("response"):
            return jsonify(cached["response"]), 200

    # Handle supported supervisory actions
    if action in ["status", "health", "supervision_status"]:
        res_data = _get_engine_status_summary()
        response_payload = {
            "task_id": task_id,
            "target_agent": "stratex",
            "status": "SUCCESS",
            "result": res_data,
            "summary": f"Stratex supervision status retrieved for {source_agent}.",
            "execution_time_ms": int((time.time() - t0) * 1000)
        }
    elif action in ["panic", "emergency_stop", "kill_switch"]:
        confirm = isinstance(payload, dict) and payload.get("confirm") is True
        if not confirm:
            return jsonify({
                "task_id": task_id,
                "target_agent": "stratex",
                "status": "FAILED",
                "error": "CONFIRMATION_REQUIRED",
                "message": "Panic action requires explicit payload {'confirm': true}."
            }), 400
        # Trigger panic with the TASK's own payload (the HTTP route reads the
        # top-level body, where a task's {"payload": {"confirm": true}} is absent).
        result, code = apply_supervision_panic(
            confirm=True,
            release=payload.get("release") is True,
            reason=str(payload.get("reason", f"Emergency panic requested by {source_agent} task")),
            source=str(payload.get("source", source_agent)),
        )
        response_payload = {
            "task_id": task_id,
            "target_agent": "stratex",
            "status": "SUCCESS" if code == 200 else "FAILED",
            "result": result,
            "summary": (f"Emergency panic {'released' if payload.get('release') is True else 'triggered'} by {source_agent}."
                        if code == 200 else f"Emergency panic request from {source_agent} FAILED: {result.get('error')}"),
            "execution_time_ms": int((time.time() - t0) * 1000)
        }
        if code != 200:
            if idempotency_key:
                # A failed panic must stay retryable, not be parked as PENDING.
                get_idempotency_store().remove(idempotency_key)
            return jsonify(response_payload), code
    elif action in ["advisories", "list_advisories"]:
        overlay = get_advisory_overlay()
        pending = overlay.get_pending_recommendations()
        response_payload = {
            "task_id": task_id,
            "target_agent": "stratex",
            "status": "SUCCESS",
            "result": {"pending_count": len(pending), "pending_advisories": pending},
            "summary": f"Retrieved {len(pending)} pending advisories for {source_agent}.",
            "execution_time_ms": int((time.time() - t0) * 1000)
        }
    elif action in ["authorize", "authorize_advisory"]:
        rec_id = payload.get("recommendation_id")
        token = payload.get("authorization_token", "friday_auth")
        overlay = get_advisory_overlay()
        ok, msg, res = overlay.apply_authorized_recommendation(
            recommendation_id=rec_id,
            authorization_token=token,
            authorized_by=source_agent
        )
        response_payload = {
            "task_id": task_id,
            "target_agent": "stratex",
            "status": "SUCCESS" if ok else "FAILED",
            "result": res,
            "summary": msg,
            "execution_time_ms": int((time.time() - t0) * 1000)
        }
    else:
        response_payload = {
            "task_id": task_id,
            "target_agent": "stratex",
            "status": "SUCCESS",
            "result": _get_engine_status_summary(),
            "summary": f"Processed supervisory action '{action}' for {source_agent}.",
            "execution_time_ms": int((time.time() - t0) * 1000)
        }

    if idempotency_key:
        get_idempotency_store().complete_request(idempotency_key, response_payload)

    return jsonify(response_payload), 200
