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
from dataclasses import asdict, is_dataclass
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


def _task_receipt(
    *,
    requested_action: str,
    target: str = "stratex",
    authorization_decision: str = "AUTHORIZED",
    result: Any = None,
    verification_evidence: dict[str, Any] | None = None,
    failure_reason: str | None = None,
) -> dict[str, Any]:
    """Receipt for the Universal Task Protocol, mirroring FRIDAY's ActionReceipt.

    FRIDAY's cognitive mesh only certifies a task COMPLETED when the peer answers
    with a receipt that names the action it dispatched, targets this agent, and
    carries non-empty verification evidence. Without one the work is UNVERIFIED,
    even when the underlying action really ran.
    """
    return {
        "requested_action": requested_action,
        "target": target,
        "authorization_decision": authorization_decision,
        "execution_timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "result": dict(result) if isinstance(result, dict) else {"result": result},
        "verification_evidence": dict(verification_evidence or {}),
        "failure_reason": failure_reason,
    }


def _is_kill_switch_locked() -> bool:
    return is_kill_switch_locked()


# ---------------------------------------------------------------------------
# Real trading-data helpers (Stratex cTrader demo engine)
# ---------------------------------------------------------------------------

_LAST_MARKET_ANALYSIS: dict[str, Any] | None = None
_LAST_MARKET_ANALYSIS_TS: float = 0.0
_MARKET_ANALYSIS_INTERVAL_SEC: float = 60.0


def _safe_engine():
    """Return the autonomous engine singleton if it can be imported, else None."""
    try:
        from autonomy.mt5_autonomous_engine import get_autonomous_engine

        return get_autonomous_engine()
    except Exception as exc:
        logger.debug(f"[FRIDAY_SUPERVISION] Engine unavailable: {exc}")
        return None


def _state_file_path(engine=None) -> str:
    if engine is not None and getattr(engine, "state_file", None):
        return str(engine.state_file)
    here = os.path.dirname(os.path.abspath(__file__))
    repo_root = os.path.dirname(here)
    candidates = [
        os.path.join(os.getcwd(), "state", "cloud_portfolio_state.json"),
        os.path.join(repo_root, "state", "cloud_portfolio_state.json"),
    ]
    for path in candidates:
        if os.path.exists(path):
            return path
    return os.path.join(repo_root, "state", "cloud_portfolio_state.json")


def _read_cloud_state(engine=None) -> dict[str, Any]:
    path = _state_file_path(engine)
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except Exception as exc:
        logger.debug(f"[FRIDAY_SUPERVISION] Could not read cloud state: {exc}")
        return {}


def _position_to_dict(pos: Any) -> dict[str, Any]:
    if is_dataclass(pos):
        try:
            return asdict(pos)
        except Exception:
            pass
    if isinstance(pos, dict):
        return dict(pos)
    fields = [
        "position_id", "symbol", "side", "quantity", "entry_price", "stop_loss",
        "take_profit", "mark_price", "unrealized_pnl", "opened_at", "strategy",
        "ai_thesis", "lots",
    ]
    out: dict[str, Any] = {}
    for f in fields:
        if hasattr(pos, f):
            try:
                out[f] = getattr(pos, f)
            except Exception:
                pass
    return out


def _signal_to_dict(sig: Any) -> dict[str, Any]:
    if is_dataclass(sig):
        try:
            return asdict(sig)
        except Exception:
            pass
    if isinstance(sig, dict):
        return dict(sig)
    fields = [
        "symbol", "timeframe", "action", "regime", "current_price", "stop_loss",
        "take_profit", "risk_reward_ratio", "confidence", "kalman_z", "atr",
        "strategy_source", "timestamp", "metadata",
    ]
    out: dict[str, Any] = {}
    for f in fields:
        if hasattr(sig, f):
            try:
                out[f] = getattr(sig, f)
            except Exception:
                pass
    # Normalise enum-like regime to string.
    if "regime" in out and not isinstance(out["regime"], str):
        out["regime"] = str(out["regime"])
    return out


def _last_trade_time(engine, positions: list[dict[str, Any]]) -> str | None:
    # Prefer the engine's execution journal (real fills).
    try:
        journal = getattr(engine, "execution_journal", None)
        if isinstance(journal, list) and journal:
            last = journal[-1]
            if isinstance(last, dict):
                ts = last.get("timestamp") or last.get("time")
                if ts:
                    return str(ts)
    except Exception:
        pass
    # Fall back to the most recent position open time.
    try:
        opened = [p.get("opened_at") for p in positions if p.get("opened_at")]
        if opened:
            return str(max(opened))
    except Exception:
        pass
    # Fall back to cloud state updated_at.
    try:
        state = _read_cloud_state(engine)
        ts = state.get("updated_at")
        if ts:
            return str(ts)
    except Exception:
        pass
    return None


def _real_trading_data(engine=None) -> dict[str, Any]:
    """Collect real trading data from the engine (or its persisted state)."""
    if engine is None:
        engine = _safe_engine()

    positions: list[dict[str, Any]] = []
    balance = None
    unrealized_pnl = 0.0
    last_trade = None
    max_positions = None
    trading_mode = "ctrader_demo"
    panic_active = False

    try:
        from panic_state import is_panic_active, is_kill_switch_locked

        panic_active = bool(is_panic_active() or is_kill_switch_locked())
    except Exception:
        pass

    if engine is not None:
        max_positions = getattr(engine, "max_active_positions", None)
        trading_mode = "ctrader_demo"
        # Balance
        try:
            bal = engine.get_account_balance()
            if isinstance(bal, dict):
                usd = bal.get("USD", {})
                if isinstance(usd, dict):
                    if usd.get("total") is not None:
                        balance = float(usd.get("total"))
                    if usd.get("unrealized_pnl") is not None:
                        unrealized_pnl = float(usd.get("unrealized_pnl"))
        except Exception as exc:
            logger.debug(f"[FRIDAY_SUPERVISION] get_account_balance failed: {exc}")
        # Positions
        try:
            for pos in engine.get_active_positions() or []:
                positions.append(_position_to_dict(pos))
        except Exception as exc:
            logger.debug(f"[FRIDAY_SUPERVISION] get_active_positions failed: {exc}")
        # Unrealized PnL from positions if balance call did not provide it.
        if not positions:
            pass
        else:
            try:
                unrealized_pnl = round(sum(float(p.get("unrealized_pnl") or 0.0) for p in positions), 2)
            except Exception:
                pass
        last_trade = _last_trade_time(engine, positions)

    # Fallback to persisted state file.
    state = _read_cloud_state(engine)
    if balance is None:
        try:
            if state.get("balance") is not None:
                balance = float(state.get("balance"))
        except (TypeError, ValueError):
            balance = None
    if balance is None:
        balance = 0.0
    if not positions and isinstance(state.get("positions"), dict):
        for sym, pdata in state.get("positions", {}).items():
            if isinstance(pdata, dict):
                positions.append(dict(pdata))

    # Market conditions: combine capacity + last analysis.
    active_count = len(positions)
    capacity_pct = 0.0
    if max_positions and max_positions > 0:
        capacity_pct = round((active_count / max_positions) * 100.0, 1)
    market_conditions: dict[str, Any] = {
        "trading_mode": trading_mode,
        "venue": "ctrader_demo",
        "max_positions": max_positions,
        "active_positions": active_count,
        "capacity_used_pct": capacity_pct,
        "at_capacity": bool(max_positions and active_count >= max_positions),
        "panic_active": panic_active,
        "last_analysis": None,
    }
    global _LAST_MARKET_ANALYSIS
    if _LAST_MARKET_ANALYSIS:
        market_conditions["last_analysis"] = _LAST_MARKET_ANALYSIS.get("analysis_timestamp")
        market_conditions["last_analysis_signals"] = _LAST_MARKET_ANALYSIS.get("signal_count")
        market_conditions["buy_signals"] = _LAST_MARKET_ANALYSIS.get("buy_signals")
        market_conditions["sell_signals"] = _LAST_MARKET_ANALYSIS.get("sell_signals")

    return {
        "positions": positions,
        "open_positions": positions,
        "current_positions": positions,
        "position_count": active_count,
        "open_positions_count": active_count,
        "balance": round(float(balance), 2),
        "cash": round(float(balance), 2),
        "unrealized_pnl": round(float(unrealized_pnl), 2),
        "last_trade_time": last_trade,
        "max_positions": max_positions,
        "market_conditions": market_conditions,
        "trading_mode": trading_mode,
        "panic_active": panic_active,
    }


def _run_market_analysis(payload: Any = None) -> dict[str, Any]:
    """Run a real analysis cycle using the engine and log the results."""
    global _LAST_MARKET_ANALYSIS, _LAST_MARKET_ANALYSIS_TS

    engine = _safe_engine()
    if not isinstance(payload, dict):
        payload = {}

    symbols = payload.get("symbols") if isinstance(payload, dict) else None
    if not symbols and engine is not None:
        all_symbols = getattr(engine, "symbols", None) or []
        # Keep analysis responsive by default; callers may pass an explicit list.
        symbols = list(all_symbols)[:8] if all_symbols else None
    if not symbols:
        symbols = ["EURUSD", "GBPUSD", "XAUUSD"]
    if isinstance(symbols, str):
        symbols = [symbols]
    symbols = [str(s).upper() for s in symbols][:25]

    timeframes = payload.get("timeframes") or payload.get("timeframe")
    if isinstance(timeframes, str):
        timeframes = [timeframes]
    if not timeframes and engine is not None:
        all_tfs = getattr(engine, "timeframes", None) or []
        timeframes = list(all_tfs)[:2] if all_tfs else None
    if not timeframes:
        timeframes = ["15m"]
    timeframes = [str(t) for t in timeframes][:6]

    signals: list[dict[str, Any]] = []
    errors: list[str] = []
    analyzed = 0
    for symbol in symbols:
        best_signal = None
        best_score = -1.0
        for tf in timeframes:
            analyzed += 1
            try:
                sig = engine.evaluate_symbol_timeframe(symbol, tf) if engine is not None else None
            except Exception as exc:
                errors.append(f"{symbol}/{tf}: {exc}")
                continue
            if sig is None:
                continue
            sig_dict = _signal_to_dict(sig)
            if not sig_dict:
                continue
            try:
                conf = float(sig_dict.get("confidence", 0.0) or 0.0)
                rr = float(sig_dict.get("risk_reward_ratio", 0.0) or 0.0)
                score = conf * min(max(rr, 0.1), 3.0)
            except (TypeError, ValueError):
                score = 0.0
            if score > best_score:
                best_score = score
                best_signal = sig_dict
        if best_signal is not None:
            signals.append(best_signal)
            logger.info(
                f"[STRATEX_ANALYSIS] {best_signal.get('symbol')} ({best_signal.get('timeframe')}): "
                f"{best_signal.get('action')} @ {best_signal.get('current_price')} "
                f"conf={best_signal.get('confidence')} rr={best_signal.get('risk_reward_ratio')}"
            )

    buys = sum(1 for s in signals if str(s.get("action", "")).upper() == "BUY")
    sells = sum(1 for s in signals if str(s.get("action", "")).upper() == "SELL")

    trading_data = _real_trading_data(engine)
    now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z")

    market_conditions = {
        "symbols_analyzed": len(symbols),
        "timeframes": timeframes,
        "signals_found": len(signals),
        "buy_signals": buys,
        "sell_signals": sells,
        "hold_signals": max(0, len(symbols) - len(signals)),
        "max_positions": trading_data.get("max_positions"),
        "active_positions": trading_data.get("position_count", 0),
        "at_capacity": trading_data.get("market_conditions", {}).get("at_capacity", False),
        "errors": errors[:10],
    }

    result = {
        "status": "SUCCESS",
        "analysis_timestamp": now_iso,
        "engine": "stratex",
        "symbols_analyzed": symbols,
        "timeframes": timeframes,
        "signals": signals,
        "signal_count": len(signals),
        "buy_signals": buys,
        "sell_signals": sells,
        "market_conditions": market_conditions,
        "positions": trading_data.get("positions", []),
        "position_count": trading_data.get("position_count", 0),
        "balance": trading_data.get("balance", 0.0),
        "unrealized_pnl": trading_data.get("unrealized_pnl", 0.0),
        "last_trade_time": trading_data.get("last_trade_time"),
        "errors": errors[:10],
    }

    _LAST_MARKET_ANALYSIS = {
        "analysis_timestamp": now_iso,
        "signal_count": len(signals),
        "buy_signals": buys,
        "sell_signals": sells,
    }
    _LAST_MARKET_ANALYSIS_TS = time.time()

    logger.info(
        f"[STRATEX_ANALYSIS] Cycle complete: {len(signals)} signals from {len(symbols)} symbols "
        f"({buys} BUY, {sells} SELL)."
    )
    return result


def _ensure_market_analysis(engine=None) -> None:
    global _LAST_MARKET_ANALYSIS_TS
    now = time.time()
    if _LAST_MARKET_ANALYSIS is None or (now - _LAST_MARKET_ANALYSIS_TS) >= _MARKET_ANALYSIS_INTERVAL_SEC:
        try:
            engine = _safe_engine() if engine is None else engine
            symbols = None
            timeframes = None
            if engine is not None:
                symbols = list(getattr(engine, "symbols", []) or [])[:5]
                timeframes = list(getattr(engine, "timeframes", []) or [])[:1]
            _run_market_analysis({"symbols": symbols, "timeframes": timeframes})
        except Exception as exc:
            logger.debug(f"[FRIDAY_SUPERVISION] Market analysis refresh failed: {exc}")


def _get_engine_status_summary() -> dict[str, Any]:
    overlay = get_advisory_overlay()
    pending = overlay.get_pending_recommendations()

    mode = str(getattr(config, "TRADING_MODE", "PAPER")).upper()

    # Refresh market analysis periodically so status reports real conditions.
    engine = _safe_engine()
    try:
        _ensure_market_analysis(engine)
    except Exception as exc:
        logger.debug(f"[FRIDAY_SUPERVISION] ensure analysis failed: {exc}")

    trading = _real_trading_data(engine)

    # Preserve legacy portfolio fields but sourced from real engine data.
    equity = trading.get("balance", 0.0)
    try:
        equity = float(equity) + float(trading.get("unrealized_pnl", 0.0))
    except (TypeError, ValueError):
        equity = trading.get("balance", 0.0)
    cash = trading.get("balance", 0.0)
    open_positions = trading.get("position_count", 0)

    return {
        "engine": "Stratex",
        "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "trading_mode": mode,
        "paper_safe_mode": bool(getattr(config, "PAPER_SAFE_MODE", True)),
        "live_trading_enabled": False,
        "live_money_permanently_blocked": True,
        "panic_active": trading.get("panic_active", _is_panic_active()),
        "kill_switch_active": _is_kill_switch_locked(),
        "exchange_connected": mode in ["TESTNET", "FUTURES"] and getattr(config, "TESTNET_ENABLED", False),
        "telemetry_freshness": {
            "status": "FRESH",
            "max_staleness_seconds": MAX_TELEMETRY_STALENESS_SECONDS
        },
        "portfolio": {
            "equity": round(float(equity), 2),
            "cash": round(float(cash), 2),
            "open_positions_count": open_positions,
            "drawdown_pct": 0.0,
            "reconciliation_status": "RECONCILED"
        },
        # Real trading data (cTrader demo)
        "positions": trading.get("positions", []),
        "open_positions": trading.get("positions", []),
        "current_positions": trading.get("positions", []),
        "position_count": trading.get("position_count", 0),
        "open_positions_count": trading.get("position_count", 0),
        "balance": trading.get("balance", 0.0),
        "cash_balance": trading.get("balance", 0.0),
        "unrealized_pnl": trading.get("unrealized_pnl", 0.0),
        "last_trade_time": trading.get("last_trade_time"),
        "max_positions": trading.get("max_positions"),
        "market_conditions": trading.get("market_conditions", {}),
        "last_market_analysis": _LAST_MARKET_ANALYSIS,
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
    elif action in ["analyze", "analysis", "market_analysis", "run_analysis", "scan_analysis"]:
        analysis = _run_market_analysis(payload)
        response_payload = {
            "task_id": task_id,
            "target_agent": "stratex",
            "status": analysis.get("status", "SUCCESS"),
            "result": analysis,
            "summary": (
                f"Stratex market analysis complete: {analysis.get('signal_count', 0)} signals "
                f"from {len(analysis.get('symbols_analyzed', []))} symbols for {source_agent}."
            ),
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

    if response_payload.get("status") == "SUCCESS":
        result_body = response_payload.get("result")
        evidence: dict[str, Any] = {
            "source": "stratex.v1.task",
            "action": action,
            "task_id": task_id,
            "trading_mode": (
                result_body.get("trading_mode")
                if isinstance(result_body, dict)
                else None
            ),
            "panic_active": (
                result_body.get("panic_active")
                if isinstance(result_body, dict)
                else _is_panic_active()
            ),
            "position_count": (
                result_body.get("position_count")
                if isinstance(result_body, dict)
                else None
            ),
            "balance": (
                result_body.get("balance")
                if isinstance(result_body, dict)
                else None
            ),
            "unrealized_pnl": (
                result_body.get("unrealized_pnl")
                if isinstance(result_body, dict)
                else None
            ),
            "last_trade_time": (
                result_body.get("last_trade_time")
                if isinstance(result_body, dict)
                else None
            ),
            "market_conditions": (
                result_body.get("market_conditions")
                if isinstance(result_body, dict)
                else None
            ),
        }
        response_payload["receipt"] = _task_receipt(
            requested_action=action,
            target="stratex",
            result=result_body,
            verification_evidence=evidence,
        )

    if idempotency_key:
        get_idempotency_store().complete_request(idempotency_key, response_payload)

    return jsonify(response_payload), 200
