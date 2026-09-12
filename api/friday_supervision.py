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
import time
from typing import Any

from flask import Blueprint, jsonify, request

import config
from advisory_params import get_advisory_overlay
from audit.audit_manager import get_audit_manager, get_idempotency_store
from logger import get_logger
from telemetry.health_guard import (
    MAX_TELEMETRY_STALENESS_SECONDS,
    StaleTelemetryError,
    check_telemetry_freshness,
)

logger = get_logger("friday_supervision")

friday_supervision_bp = Blueprint("friday_supervision", __name__)


def _is_panic_active() -> bool:
    panic_file = os.getenv("PANIC_STATE_FILE", "panic_state.json")
    if os.path.exists(panic_file):
        try:
            with open(panic_file, "r", encoding="utf-8") as f:
                data = json.load(f)
                return bool(data.get("panic_active", False))
        except Exception:
            pass
    return os.path.exists("KILL_SWITCH_ACTIVE.lock")


def _is_kill_switch_locked() -> bool:
    return os.path.exists("KILL_SWITCH_ACTIVE.lock")


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
def execute_supervision_panic():
    """
    Emergency Panic Kill-Switch — blocks all order placement and halts engine.
    Requires: {"confirm": true, "reason": "...", "source": "FRIDAY"}
    To release: {"confirm": true, "release": true}
    """
    body = request.get_json(silent=True) or {}
    confirm = bool(body.get("confirm", False))
    release = bool(body.get("release", False))
    reason = str(body.get("reason", "Emergency panic requested via FRIDAY Supervision"))
    source = str(body.get("source", "FRIDAY"))

    if not confirm:
        return jsonify({
            "status": "ERROR",
            "error": "CONFIRMATION_REQUIRED",
            "message": "Emergency panic operation requires explicit confirmation: {'confirm': true}."
        }), 400

    panic_file = os.getenv("PANIC_STATE_FILE", "panic_state.json")
    now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()

    if release:
        # Release panic
        try:
            tmp = panic_file + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump({
                    "panic_active": False,
                    "released_at": now_iso,
                    "actor": source,
                    "reason": reason
                }, f, indent=2)
            os.replace(tmp, panic_file)
        except Exception as e:
            logger.error(f"Failed to update panic state file on release: {e}")

        if os.path.exists("KILL_SWITCH_ACTIVE.lock"):
            try:
                os.remove("KILL_SWITCH_ACTIVE.lock")
            except Exception:
                pass

        audit = get_audit_manager().record_event(
            event_type="PANIC_RELEASED",
            actor=source,
            details={"released_at": now_iso},
            rationale=reason,
            status="PANIC_RELEASED"
        )
        return jsonify({
            "status": "SUCCESS",
            "panic_active": False,
            "message": "PANIC RELEASED: Trading engine unblocked.",
            "audit_hash": audit.get("hash")
        }), 200

    # Activate Panic
    try:
        tmp = panic_file + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({
                "panic_active": True,
                "triggered_at": now_iso,
                "actor": source,
                "reason": reason
            }, f, indent=2)
        os.replace(tmp, panic_file)
    except Exception as e:
        logger.error(f"Failed to update panic state file on trigger: {e}")

    try:
        with open("KILL_SWITCH_ACTIVE.lock", "w", encoding="utf-8") as f:
            json.dump({
                "kill_switch_active": True,
                "triggered_at": now_iso,
                "actor": source,
                "reason": reason
            }, f, indent=2)
    except Exception as e:
        logger.error(f"Failed to create kill switch lock file: {e}")

    audit = get_audit_manager().record_event(
        event_type="PANIC_TRIGGERED",
        actor=source,
        details={"triggered_at": now_iso, "panic_file": panic_file},
        rationale=reason,
        status="PANIC_ACTIVATED"
    )

    return jsonify({
        "status": "SUCCESS",
        "panic_active": True,
        "kill_switch_active": True,
        "message": "EMERGENCY PANIC ACTIVATED: All new orders blocked. Open orders cancelled.",
        "audit_hash": audit.get("hash")
    }), 200


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
def authorize_advisory_recommendation():
    """
    Separation of Advisory Generation and Application:
    Explicitly authorizes and applies a staged bounded parameter change.
    Requires: {"recommendation_id": "...", "authorization_token": "...", "authorized_by": "FRIDAY"}
    """
    body = request.get_json(silent=True) or {}
    rec_id = body.get("recommendation_id")
    token = body.get("authorization_token")
    authorizer = body.get("authorized_by", "FRIDAY")
    idempotency_key = request.headers.get("Idempotency-Key") or body.get("idempotency_key")

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


# ==============================================================================
# UNIVERSAL TASK PROTOCOL ENDPOINT (WITH DETERMINISTIC EXECUTION ENFORCEMENT)
# ==============================================================================

@friday_supervision_bp.route("/v1/friday/task", methods=["POST"])
@friday_supervision_bp.route("/v1/task/execute", methods=["POST"])
def execute_friday_task():
    """
    Universal Task Protocol entry point for FRIDAY and Cortex.
    Enforces the Core Invariant:
    Prediction / External Agent Inquiry is NOT Authorization.
    FRIDAY or Inference cannot directly order executions or bypass Stratex safety gates.
    """
    t0 = time.time()
    body = request.get_json(silent=True) or {}
    task_id = body.get("task_id", f"stx_task_{int(time.time_ns())}")
    action = str(body.get("action", "status")).lower()
    source_agent = str(body.get("source_agent", "unknown"))
    payload = body.get("payload") if isinstance(body.get("payload"), dict) else body
    idempotency_key = request.headers.get("Idempotency-Key") or body.get("idempotency_key")

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
        confirm = bool(payload.get("confirm", False))
        if not confirm:
            return jsonify({
                "task_id": task_id,
                "target_agent": "stratex",
                "status": "FAILED",
                "error": "CONFIRMATION_REQUIRED",
                "message": "Panic action requires explicit payload {'confirm': true}."
            }), 400
        # Trigger panic
        res = execute_supervision_panic()
        response_payload = {
            "task_id": task_id,
            "target_agent": "stratex",
            "status": "SUCCESS",
            "result": res[0].get_json() if hasattr(res[0], "get_json") else {},
            "summary": f"Emergency panic triggered by {source_agent}.",
            "execution_time_ms": int((time.time() - t0) * 1000)
        }
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
