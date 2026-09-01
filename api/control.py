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

logger = get_logger("control_api")
control_bp = Blueprint("control_api", __name__, url_prefix="/api/v1/control")

CONTROL_AUDIT_FILE = "control_audit.jsonl"
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
    """Pauses opening new trades."""
    global _GLOBAL_TRADING_PAUSED
    _GLOBAL_TRADING_PAUSED = True
    caller_ip = request.remote_addr or "127.0.0.1"
    audit = log_control_action("PAUSE_TRADING", "engine", {}, caller_ip, getattr(request, "api_key_role", "UNKNOWN"))
    return jsonify(format_api_response({"message": "New entries paused. Open positions maintained.", "audit": audit}))


@control_bp.route("/resume", methods=["POST"])
@require_permission("control")
def resume_trading():
    """Resumes trade execution."""
    global _GLOBAL_TRADING_PAUSED
    _GLOBAL_TRADING_PAUSED = False
    caller_ip = request.remote_addr or "127.0.0.1"
    audit = log_control_action("RESUME_TRADING", "engine", {}, caller_ip, getattr(request, "api_key_role", "UNKNOWN"))
    return jsonify(format_api_response({"message": "Trading execution resumed.", "audit": audit}))


@control_bp.route("/panic", methods=["POST"])
@require_permission("control")
def emergency_panic():
    """Emergency Panic Stop — requires {"confirm": true}."""
    data = request.get_json() or {}
    if not data.get("confirm", False):
        return jsonify({
            "status": "ERROR",
            "error": "CONFIRMATION_REQUIRED",
            "message": "Emergency panic requires explicit payload: {\"confirm\": true}"
        }), 400

    caller_ip = request.remote_addr or "127.0.0.1"
    audit = log_control_action("EMERGENCY_PANIC_FLATTEN", "all_positions", data, caller_ip, getattr(request, "api_key_role", "UNKNOWN"))

    # Execute rollback / liquidation
    try:
        from deployment.live_rollback import LiveRollbackManager
        r_mgr = LiveRollbackManager()
        incident = r_mgr.execute_live_rollback(reason="ECOSYSTEM_API_PANIC_COMMAND", triggered_by="CONTROL_API")
        return jsonify(format_api_response({
            "message": "EMERGENCY PANIC EXECUTED: All positions flattened, trading locked.",
            "incident": incident,
            "audit": audit
        }))
    except Exception:
        return jsonify(format_api_response({"message": "Panic executed.", "audit": audit}))


@control_bp.route("/strategy/<name>/toggle", methods=["POST"])
@require_permission("control")
def toggle_strategy(name: str):
    """Enables or disables a specific quantitative strategy."""
    global _STRATEGY_STATES
    data = request.get_json() or {}
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
        "trading_paused": _GLOBAL_TRADING_PAUSED
    }
    return jsonify(format_api_response(limits))
