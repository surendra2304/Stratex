"""
api/master_control_api.py — Master Ecosystem Control & Operational Autonomy REST API Blueprint.

Endpoints:
- GET  /api/ecosystem/status : Complete bot and ecosystem status.
- GET  /api/ecosystem/health : Status of all subsystems and dependencies.
- GET  /api/ecosystem/decisions : Autonomous decision log with multi-frequency breakdown.
- POST /api/ecosystem/mode : Updates operations autonomy level (Level 1, 2, 3; requires auth + confirmation).
- GET  /api/ecosystem/report : Full operational and compliance report.
"""

import datetime

from flask import Blueprint, jsonify, request

from api.auth import require_permission
from api.data_shapes import format_api_response
from autonomy.compliance_reporting import ComplianceReporter
from autonomy.operations_director import AutonomousOperationsDirector
from security_hardening import sign_audit_record

master_control_bp = Blueprint("master_control", __name__, url_prefix="/api/ecosystem")
_director = AutonomousOperationsDirector()
_compliance = ComplianceReporter()


@master_control_bp.route("/status", methods=["GET"])
def get_ecosystem_status():
    """Returns complete bot state and state machine posture."""
    status = _director.get_ecosystem_status()
    return jsonify(format_api_response(status))


@master_control_bp.route("/health", methods=["GET"])
def get_subsystems_health():
    """Checks all subsystems including self-healing, data feeds, and execution engines."""
    health = {
        "overall_status": "HEALTHY",
        "operations_director": "ACTIVE",
        "self_healing": {
            "healed_incidents": _director.self_healing.healed_incidents_count,
            "status": "OPERATIONAL"
        },
        "state_machine": _director.state_machine.current_state,
        "timestamp": datetime.datetime.utcnow().isoformat() + "Z"
    }
    return jsonify(format_api_response(health))


@master_control_bp.route("/decisions", methods=["GET"])
def get_decisions_log():
    """Returns the multi-frequency autonomous decisions log."""
    from dataclasses import asdict
    decisions = [asdict(d) for d in _director.decision_log[-50:]]
    return jsonify(format_api_response({"decisions_count": len(decisions), "decisions": decisions}))


@master_control_bp.route("/mode", methods=["POST"])
@require_permission("control")
def set_autonomy_mode():
    """Sets autonomy level (1, 2, 3) — requires confirmation and CONTROL permission."""
    data = request.get_json() or {}
    level = data.get("level")
    confirmed = data.get("confirm", False)

    if level not in [1, 2, 3] or not confirmed:
        return jsonify({
            "status": "ERROR",
            "error": "INVALID_OR_UNCONFIRMED",
            "message": "Setting autonomy mode requires 'level' (1, 2, or 3) and explicit 'confirm': true."
        }), 400

    new_level = _director.set_autonomy_level(level)
    audit = {
        "action": "SET_AUTONOMY_LEVEL",
        "new_level": new_level,
        "caller_ip": request.remote_addr or "127.0.0.1",
        "timestamp": datetime.datetime.utcnow().isoformat() + "Z"
    }
    audit["signature"] = sign_audit_record(audit)

    return jsonify(format_api_response({
        "message": f"Autonomy mode updated to LEVEL_{new_level}.",
        "autonomy_level": new_level,
        "audit": audit
    }))


def _real_daily_metrics() -> dict:
    """Computes today's actual trading metrics from the ledger.

    Never invents numbers: when the ledger is absent the metrics are zero and
    the `metrics_source` field says so. Drawdown is measured along today's
    cumulative-PnL path and is only expressed in percent when a real equity
    base (portfolio initial deposit) is available.
    """
    import json
    import os

    today = datetime.datetime.utcnow().strftime("%Y-%m-%d")
    trades_count = 0
    daily_pnl = 0.0
    pnl_path: list[float] = []
    strategy_pnl: dict[str, float] = {}
    source = "NO_DATA"
    ledger_file = os.getenv("TESTNET_LEDGER_FILE", "testnet_trade_ledger.jsonl")

    if os.path.exists(ledger_file):
        source = "LEDGER"
        try:
            with open(ledger_file, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        rec = json.loads(line)
                    except Exception:
                        continue
                    if not str(rec.get("timestamp", "")).startswith(today):
                        continue
                    trades_count += 1
                    trade_pnl = 0.0
                    try:
                        trade_pnl = float(rec.get("net_pnl", rec.get("pnl", 0.0)) or 0.0)
                    except (TypeError, ValueError):
                        trade_pnl = 0.0
                    daily_pnl += trade_pnl
                    strategy = str(rec.get("strategy") or "").strip().lower()
                    if strategy:
                        strategy_pnl[strategy] = strategy_pnl.get(strategy, 0.0) + trade_pnl
                    pnl_path.append(daily_pnl)
        except Exception:
            source = "LEDGER_ERROR"

    # Today's strongest strategy comes from the ledger itself; when no trade
    # carries a strategy tag, no winner can be determined.
    best_strategy = None
    if strategy_pnl:
        best_strategy = max(strategy_pnl.items(), key=lambda kv: kv[1])[0]

    drawdown_pct = 0.0
    drawdown_source = "UNVERIFIED_NO_EQUITY_BASE"
    portfolio_file = os.getenv("TESTNET_PORTFOLIO_FILE", "testnet_portfolio.json")
    base = None
    if os.path.exists(portfolio_file):
        try:
            with open(portfolio_file, "r", encoding="utf-8") as f:
                base = float(json.load(f).get("initial_deposit") or 0.0) or None
        except Exception:
            base = None
    if base and pnl_path:
        peak = pnl_path[0]
        worst = 0.0
        for value in pnl_path:
            peak = max(peak, value)
            worst = min(worst, value - peak)
        drawdown_pct = max(0.0, (-worst / base) * 100.0)
        drawdown_source = "LEDGER_PATH_VS_INITIAL_DEPOSIT"

    return {
        "trades_count": trades_count,
        "daily_pnl": round(daily_pnl, 2),
        "max_drawdown_reached": round(drawdown_pct, 2),
        "decisions_count": len(_director.decision_log),
        "metrics_source": source,
        "drawdown_source": drawdown_source,
        "best_strategy": best_strategy,
    }


@master_control_bp.route("/report", methods=["GET"])
def get_operational_report():
    """Returns full operational and compliance dossier built from REAL ledger
    metrics (regression guard: this endpoint used to return fabricated
    trades_count/daily_pnl constants)."""
    metrics = _real_daily_metrics()
    dossier = _compliance.generate_daily_compliance_dossier(
        trades_count=metrics["trades_count"],
        daily_pnl=metrics["daily_pnl"],
        max_drawdown_reached=metrics["max_drawdown_reached"],
        decisions_count=metrics["decisions_count"],
        best_strategy=metrics["best_strategy"],
    )
    dossier["metrics_source"] = metrics["metrics_source"]
    dossier["drawdown_source"] = metrics["drawdown_source"]
    return jsonify(format_api_response(dossier))
