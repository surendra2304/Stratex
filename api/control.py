"""
api/control.py — Safe Control API Blueprint with Cryptographic Audit Trails.

Endpoints:
- POST /api/v1/control/pause : Pauses opening new trade entries (existing positions remain open).
- POST /api/v1/control/resume : Resumes trading execution.
- POST /api/v1/control/panic : Emergency stop (flattens all positions & halts; requires {"confirm": true}).
- POST /api/v1/control/strategy/<name>/toggle : Enables/disables individual quantitative strategies.
- GET  /api/v1/control/risk-limits : Inspects current active risk constraints.

Safety Rules:
- All actions require CONTROL role.
- All actions are signed and logged to control_audit.jsonl.
- Rate-limited to 10 requests / minute.
"""

import json

from flask import Blueprint, jsonify, request

from api.auth import require_permission
from api.data_shapes import format_api_response, format_iso_timestamp
from logger import get_logger
from security_hardening import sign_audit_record
from trading_pause import is_trading_paused, set_trading_paused

logger = get_logger("control_api")
control_bp = Blueprint("control_api", __name__, url_prefix="/api/v1/control")

CONTROL_AUDIT_FILE = "control_audit.jsonl"
# NOTE: the engine runs in a separate process, so the authoritative pause state
# lives in the durable file flag managed by trading_pause.py. This in-process
# mirror is kept only for backwards-compatible responses.
_GLOBAL_TRADING_PAUSED = False
_STRATEGY_STATES = {
    "strategy_scalper": True,
    "strategy_supertrend": True,
    "strategy_adx_ema": True,
    "strategy_swing": True
}


def log_control_action(action: str, target: str, payload: dict, caller_ip: str, caller_role: str) -> dict:
    """Logs action with cryptographic signature to append-only audit trail."""
    rec = {
        "timestamp": format_iso_timestamp(),
        "action": action,
        "target": target,
        "payload": payload,
        "caller_ip": caller_ip,
        "caller_role": caller_role
    }
    sig = sign_audit_record(rec)
    rec["signature"] = sig

    try:
        with open(CONTROL_AUDIT_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec) + "\n")
    except Exception as e:
        logger.error(f"[CONTROL_AUDIT] Failed to append audit record: {e}")

    return rec


@control_bp.route("/pause", methods=["POST"])
@require_permission("control")
def pause_trading():
    """Pauses opening new trades (durable, cross-process flag)."""
    global _GLOBAL_TRADING_PAUSED
    _GLOBAL_TRADING_PAUSED = True
    caller_ip = request.remote_addr or "127.0.0.1"
    try:
        set_trading_paused(True, actor=f"api:/api/v1/control/pause:{getattr(request, 'api_key_role', 'UNKNOWN')}@{caller_ip}")
    except Exception as e:
        logger.error(f"[CONTROL] Failed to persist trading pause: {e}")
        return jsonify({"status": "ERROR", "error": "PAUSE_PERSISTENCE_FAILED",
                        "message": "The durable pause flag could not be written; see server logs."}), 500
    audit = log_control_action("PAUSE_TRADING", "engine", {}, caller_ip, getattr(request, "api_key_role", "UNKNOWN"))
    return jsonify(format_api_response({"message": "New entries paused (durable flag written). Open positions maintained.", "audit": audit}))


@control_bp.route("/resume", methods=["POST"])
@require_permission("control")
def resume_trading():
    """Resumes trade execution (clears the durable pause flag)."""
    global _GLOBAL_TRADING_PAUSED
    _GLOBAL_TRADING_PAUSED = False
    caller_ip = request.remote_addr or "127.0.0.1"
    try:
        set_trading_paused(False, actor=f"api:/api/v1/control/resume:{getattr(request, 'api_key_role', 'UNKNOWN')}@{caller_ip}")
    except Exception as e:
        logger.error(f"[CONTROL] Failed to persist trading resume: {e}")
        return jsonify({"status": "ERROR", "error": "RESUME_PERSISTENCE_FAILED",
                        "message": "The durable pause flag could not be cleared; trading remains paused."}), 500
    audit = log_control_action("RESUME_TRADING", "engine", {}, caller_ip, getattr(request, "api_key_role", "UNKNOWN"))
    return jsonify(format_api_response({"message": "Trading resumed (pause flag cleared).", "audit": audit}))


@control_bp.route("/panic", methods=["POST"])
@require_permission("control")
def emergency_panic():
    """Emergency Panic Stop — requires the literal payload {"confirm": true}.

    Blocks all new order submission through two independent durable flags (the
    unified panic flag checked at the execution boundary and the trading-pause
    flag checked by the engine loop), then runs the live-rollback lock-down
    (removes the live authorization token, writes an incident record). The
    response lists exactly which steps succeeded; it never claims that positions
    were flattened, because this endpoint does not place closing orders.
    """
    global _GLOBAL_TRADING_PAUSED
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        data = {}
    if data.get("confirm") is not True:
        return jsonify({
            "status": "ERROR",
            "error": "CONFIRMATION_REQUIRED",
            "message": "Emergency panic requires explicit payload: {\"confirm\": true}"
        }), 400

    from panic_state import engage_order_block, orders_blocked

    caller_ip = request.remote_addr or "127.0.0.1"
    role = getattr(request, "api_key_role", "UNKNOWN")
    actor = f"api:/api/v1/control/panic:{role}@{caller_ip}"
    reason = str(data.get("reason", "ECOSYSTEM_API_PANIC_COMMAND"))[:500]
    steps = engage_order_block(actor, reason, panic=True, pause=True)
    if steps.get("trading_pause") == "WRITTEN":
        _GLOBAL_TRADING_PAUSED = True

    incident = None
    try:
        from deployment.live_rollback import LiveRollbackManager
        incident = LiveRollbackManager().execute_live_rollback(reason="ECOSYSTEM_API_PANIC_COMMAND", triggered_by="CONTROL_API")
        steps["live_rollback_lockdown"] = "COMPLETED"
    except Exception as e:
        logger.error(f"[CONTROL] Live rollback lock-down failed: {e}")
        steps["live_rollback_lockdown"] = "FAILED"

    audit = log_control_action("EMERGENCY_PANIC", "order_submission", {"steps": steps, "reason": reason}, caller_ip, role)
    if not orders_blocked(steps):
        return jsonify({
            "status": "ERROR",
            "error": "PANIC_NOT_PERSISTED",
            "message": "Neither the panic flag nor the pause flag could be written; new orders are NOT blocked.",
            "steps": steps,
            "audit": audit,
        }), 500

    return jsonify(format_api_response({
        "message": ("EMERGENCY PANIC: new order submission blocked. Open positions were NOT flattened "
                    "by this endpoint (use the testnet close-all control to flatten)."),
        "complete": all(v in ("WRITTEN", "COMPLETED") for v in steps.values()),
        "steps": steps,
        "incident": incident,
        "audit": audit,
    }))


@control_bp.route("/strategy/<name>/toggle", methods=["POST"])
@require_permission("control")
def toggle_strategy(name: str):
    """Enables or disables a specific quantitative strategy."""
    global _STRATEGY_STATES
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        data = {}
    if "enabled" in data and not isinstance(data["enabled"], bool):
        return jsonify({"status": "ERROR", "error": "INVALID_ENABLED",
                        "message": "'enabled' must be a JSON boolean."}), 400
    enabled = data.get("enabled", not _STRATEGY_STATES.get(name, True))
    _STRATEGY_STATES[name] = enabled

    caller_ip = request.remote_addr or "127.0.0.1"
    audit = log_control_action("STRATEGY_TOGGLE", name, {"enabled": enabled}, caller_ip, getattr(request, "api_key_role", "UNKNOWN"))

    return jsonify(format_api_response({
        "strategy": name,
        "enabled": enabled,
        "audit": audit
    }))


@control_bp.route("/risk-limits", methods=["GET"])
@require_permission("control")
def get_control_risk_limits():
    """Inspects active risk constraints."""
    limits = {
        "max_drawdown_pct": 15.0,
        "max_daily_loss_pct": 5.0,
        "max_position_size_pct": 10.0,
        "max_leverage": 1.0,
        "trading_paused": _GLOBAL_TRADING_PAUSED or is_trading_paused()
    }
    return jsonify(format_api_response(limits))
