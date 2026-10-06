"""
api/health.py — Consumer-Agnostic Multi-Level Health & Diagnostics API Blueprint.

Endpoints:
- GET /api/v1/health : Fast liveness probe.
- GET /api/v1/health/detailed : Multi-pillar diagnostics across exchange connectivity, memory/CPU, storage, risk, and advisory subsystems.
- GET /api/v1/health/integrations : Status of external dependencies (AI-Universe connectivity and Exchange APIs).

TRUTHFULNESS CONTRACT
---------------------
Every field in these payloads is either (a) measured here at request time, or
(b) explicitly labelled UNVERIFIED / NOT_CONFIGURED / UNAVAILABLE. This module
previously returned hardcoded "HEALTHY" for four exchanges it never contacted, a
"REAL_TIME_STREAMING" feed with a 0.4s tick age during a total data blackout, an
evolution engine at "generation 14" that was not running, and a 50 GB free-disk
figure that contradicted the real value in the same response. A health endpoint
that cannot distinguish "measured good" from "not measured" is worse than no
health endpoint, because it is the one an operator or an upstream orchestrator
trusts most.
"""

import os
import shutil
import subprocess
import tempfile
import time
from datetime import datetime, timezone

from flask import Blueprint, jsonify

from api.auth import require_permission
from api.data_shapes import format_api_response
from monitoring_system import get_monitoring_system

health_bp = Blueprint("ecosystem_health", __name__, url_prefix="/api/v1/health")

# Severity ordering used to fold per-pillar statuses into one overall status.
_SEVERITY = {
    "HEALTHY": 0, "OPERATIONAL": 0, "REAL_TIME_STREAMING": 0, "CONNECTED": 0,
    "NOT_CONFIGURED": 1, "UNVERIFIED": 1, "UNAVAILABLE": 1, "STALE": 1,
    "NOT_RUNNING": 1, "DEGRADED": 1,
    "WARNING": 2,
    "CRITICAL": 3, "ERROR": 3,
}


def _worst(*statuses: str) -> str:
    """Returns the most severe of the given status strings."""
    return max(statuses, key=lambda s: _SEVERITY.get(str(s), 2)) if statuses else "UNVERIFIED"


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _resolve_version() -> str:
    """Reports a real version source instead of an invented constant."""
    try:
        sha = subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"], stderr=subprocess.DEVNULL, timeout=5
        ).decode("ascii", "replace").strip()
        if sha:
            return f"git:{sha}"
    except Exception:
        pass
    return "UNVERSIONED"


def _measure_system_resources() -> tuple[dict, str]:
    """Returns (resources, status). Never substitutes plausible-looking constants."""
    disk = shutil.disk_usage(os.getcwd())
    out: dict = {
        "timestamp": _utc_now_iso(),
        "process_pid": os.getpid(),
        "disk_percent": round((disk.used / disk.total) * 100.0, 1) if disk.total else 0.0,
        "disk_free_gb": round(disk.free / (1024 ** 3), 2),
    }
    try:
        import psutil
    except ImportError:
        # Declaring this honestly is the whole point: an unmeasured CPU is not 4.5%.
        out["cpu_percent"] = None
        out["memory_percent"] = None
        out["memory_used_mb"] = None
        out["memory_total_mb"] = None
        out["rss_memory_mb"] = None
        out["source"] = "UNAVAILABLE:psutil_not_installed"
        return out, "UNAVAILABLE"

    try:
        proc = psutil.Process()
        mem = psutil.virtual_memory()
        out["cpu_percent"] = round(psutil.cpu_percent(interval=None), 1)
        out["memory_percent"] = round(mem.percent, 1)
        out["memory_used_mb"] = round(mem.used / (1024 * 1024), 1)
        out["memory_total_mb"] = round(mem.total / (1024 * 1024), 1)
        out["rss_memory_mb"] = round(proc.memory_info().rss / (1024 * 1024), 2)
        out["source"] = "psutil"
        worst = "HEALTHY"
        if out["cpu_percent"] >= 80.0 or out["memory_percent"] >= 80.0:
            worst = "WARNING"
        if out["disk_percent"] >= 85.0:
            worst = _worst(worst, "WARNING")
        return out, worst
    except Exception as exc:
        out.update({
            "cpu_percent": None, "memory_percent": None, "memory_used_mb": None,
            "memory_total_mb": None, "rss_memory_mb": None,
            "source": f"UNAVAILABLE:{type(exc).__name__}",
        })
        return out, "UNAVAILABLE"


def _measure_exchange_connectivity() -> tuple[dict, str]:
    """Reports what is actually constructed/configured, not a hardcoded HEALTHY."""
    status: dict = {}
    try:
        from data_client import MarketDataClient
        mdc = MarketDataClient()
        status["binance"] = "CONNECTED" if mdc.is_available() else "UNAVAILABLE"
        status["binance_data_source"] = getattr(mdc, "data_source", "UNKNOWN")
        status["binance_public_source"] = getattr(mdc, "public_data_source", "UNKNOWN")
    except Exception as exc:
        status["binance"] = f"ERROR:{type(exc).__name__}"

    # These are only "configured" if credentials exist. Nothing here proves the
    # remote API answered, so the honest label is NOT_CONFIGURED / UNVERIFIED.
    status["bybit"] = "UNVERIFIED" if os.getenv("BYBIT_API_KEY") else "NOT_CONFIGURED"
    status["okx"] = "UNVERIFIED" if os.getenv("OKX_API_KEY") else "NOT_CONFIGURED"
    status["coinbase"] = "NOT_CONFIGURED"
    worst = _worst(*[v for k, v in status.items() if not k.endswith("_source")])
    return status, worst


def _measure_feed_freshness() -> tuple[dict, str]:
    """Derives feed age from the engine heartbeat, the only real evidence on disk."""
    hb_file = (
        os.getenv("TESTNET_HEARTBEAT_FILE")
        or ("testnet_heartbeat.json" if os.path.exists("testnet_heartbeat.json") else "heartbeat.json")
    )
    if not os.path.exists(hb_file):
        return {"feed_status": "UNAVAILABLE", "reason": "HEARTBEAT_FILE_MISSING", "heartbeat_file": hb_file}, "UNAVAILABLE"
    try:
        import json
        with open(hb_file, "r", encoding="utf-8") as fh:
            hb = json.load(fh)
    except Exception as exc:
        return {"feed_status": "UNAVAILABLE", "reason": f"HEARTBEAT_UNREADABLE:{type(exc).__name__}"}, "UNAVAILABLE"

    out: dict = {"feed_status": "UNVERIFIED", "heartbeat_file": hb_file}
    ages = []
    for src_key, out_key in (("last_market_update", "last_market_update_age_sec"),
                             ("last_candle_close", "last_candle_close_age_sec")):
        raw = hb.get(src_key)
        if not raw:
            out[out_key] = None
            continue
        try:
            ts = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            age = (datetime.now(timezone.utc) - ts).total_seconds()
            out[out_key] = round(age, 1)
            ages.append(age)
        except Exception:
            out[out_key] = None

    if not ages:
        out["feed_status"] = "UNAVAILABLE"
        out["reason"] = "NO_TIMESTAMPED_FEED_EVIDENCE"
        return out, "UNAVAILABLE"

    newest = min(ages)
    out["newest_feed_evidence_age_sec"] = round(newest, 1)
    if newest <= 900:
        out["feed_status"] = "REAL_TIME_STREAMING"
        return out, "HEALTHY"
    out["feed_status"] = "STALE"
    return out, "STALE"


def _measure_storage() -> tuple[dict, str]:
    """Actually probes write access and ledger appendability instead of asserting True."""
    disk = shutil.disk_usage(os.getcwd())
    out: dict = {"disk_free_gb": round(disk.free / (1024 ** 3), 2)}

    try:
        probe_dir = tempfile.mkdtemp(prefix="stratex_health_")
        probe = os.path.join(probe_dir, "write_probe")
        with open(probe, "w", encoding="utf-8") as fh:
            fh.write("ok")
        os.remove(probe)
        os.rmdir(probe_dir)
        out["state_accessible"] = True
    except Exception as exc:
        out["state_accessible"] = False
        out["state_accessible_error"] = f"{type(exc).__name__}: {exc}"

    ledger = os.getenv("TESTNET_LEDGER_FILE", "testnet_trade_ledger.jsonl")
    try:
        with open(ledger, "a", encoding="utf-8"):
            pass
        out["ledgers_appendable"] = True
    except Exception as exc:
        out["ledgers_appendable"] = False
        out["ledgers_appendable_error"] = f"{type(exc).__name__}: {exc}"
    out["ledger_file"] = ledger

    status = "HEALTHY" if (out["state_accessible"] and out["ledgers_appendable"]) else "CRITICAL"
    if out["disk_free_gb"] < 1.0:
        status = _worst(status, "WARNING")
    return out, status


def _measure_evolution_engine() -> tuple[dict, str]:
    """No evolution engine runs inside this process; say so rather than inventing a generation."""
    return {
        "status": "NOT_RUNNING",
        "reason": "No GeneticEvolutionEngine instance is hosted by this web process.",
        "active_population": None,
        "current_generation": None,
    }, "NOT_RUNNING"


@health_bp.route("", methods=["GET"])
def fast_liveness():
    """Ultra-fast liveness check without heavy database or network queries."""
    observed_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    return jsonify({
        "status": "HEALTHY",
        "timestamp": time.time(),  # Backward-compatible epoch timestamp.
        "observed_at": observed_at,
        "evidence_class": "process_liveness",
        "readiness_assessed": False,
        "bot_mode": os.getenv("TRADING_MODE", "TESTNET").upper()
    })


@health_bp.route("/detailed", methods=["GET"])
@require_permission("read")
def detailed_diagnostics():
    """Full multi-pillar system diagnostics. Every pillar is measured or labelled unverified."""
    mon = get_monitoring_system()
    trading = mon.get_trading_health_metrics()
    advisory = mon.get_advisory_health_metrics()

    resources, resources_status = _measure_system_resources()
    exchanges, exchanges_status = _measure_exchange_connectivity()
    feed, feed_status = _measure_feed_freshness()
    storage, storage_status = _measure_storage()
    evolution, evolution_status = _measure_evolution_engine()

    circuit_breakers = None
    try:
        from exchanges.health_monitor import MultiExchangeHealthMonitor
        monitor = MultiExchangeHealthMonitor(exchange_ids=["binance"])
        statuses = monitor.get_all_health_statuses() or {}
        circuit_breakers = sum(
            1 for s in statuses.values()
            if isinstance(s, dict) and s.get("circuit_breaker_open")
        )
    except Exception:
        circuit_breakers = None

    risk_system = {
        "status": trading.get("status", "UNVERIFIED"),
        "drawdown_pct": trading.get("drawdown_pct"),
        "daily_loss_pct": trading.get("daily_loss_pct"),
        "open_positions": trading.get("open_positions"),
        "circuit_breakers_tripped": circuit_breakers,
        "circuit_breaker_source": "measured" if circuit_breakers is not None else "UNVERIFIED",
    }

    start_time = getattr(mon, "start_time", None)
    diagnostics = {
        # Fold every pillar in. Previously this looked only at `trading`, so a
        # WARNING advisory pillar still produced overall_status HEALTHY.
        "overall_status": _worst(
            trading.get("status", "UNVERIFIED"),
            advisory.get("status", "UNVERIFIED"),
            resources_status, exchanges_status, feed_status, storage_status, evolution_status,
        ),
        "version": _resolve_version(),
        "uptime_seconds": round(time.time() - start_time, 1) if start_time else None,
        "uptime_source": "measured" if start_time else "UNVERIFIED",
        "system_resources": resources,
        "system_resources_status": resources_status,
        "exchange_connectivity": exchanges,
        "exchange_connectivity_status": exchanges_status,
        "data_feed_freshness": feed,
        "data_feed_freshness_status": feed_status,
        "trading_engine": trading,
        "ai_advisory": advisory,
        "risk_system": risk_system,
        "evolution_engine": evolution,
        "storage": storage,
        "storage_status": storage_status,
        "evidence_class": "measured_at_request_time",
        "observed_at": _utc_now_iso(),
    }
    return jsonify(format_api_response(diagnostics))


@health_bp.route("/integrations", methods=["GET"])
@require_permission("read")
def integration_dependencies():
    """Reports which external dependencies are CONFIGURED.

    Reachability is deliberately not asserted: claiming "CONNECTED" without a
    probe is how this endpoint came to report a healthy AI-Universe while the
    advisory pillar in /detailed showed ai_universe_online=false.
    """
    def _peer(name: str, url_envs: tuple[str, ...], key_envs: tuple[str, ...]) -> dict:
        url = next((os.getenv(e) for e in url_envs if os.getenv(e)), None)
        key = next((os.getenv(e) for e in key_envs if os.getenv(e)), None)
        return {
            "target": url,
            "configured": bool(url),
            "credentials_present": bool(key),
            "status": "CONFIGURED" if url else "NOT_CONFIGURED",
            "reachability": "UNVERIFIED",
        }

    integrations = {
        "ai_universe": _peer(
            "ai_universe",
            ("INFERENCE_URL", "AI_UNIVERSE_URL", "AI_UNIVERSE_BASE_URL"),
            ("INFERENCE_API_KEY", "AI_UNIVERSE_API_KEY"),
        ),
        "memora": _peer("memora", ("MEMORA_URL",), ("MEMORA_API_KEY",)),
        "intelx": _peer("intelx", ("INTELX_URL", "INTELX_BASE_URL"), ("INTELX_API_KEY",)),
        "futuris": _peer("futuris", ("FUTURIS_URL", "FUTURIS_BASE_URL"), ("FUTURIS_API_KEY",)),
        "webhook_service": {
            "configured": bool(os.getenv("WEBHOOK_URLS")),
            "status": "CONFIGURED" if os.getenv("WEBHOOK_URLS") else "NOT_CONFIGURED",
        },
        "exchange_apis": {
            # Only binance has a client in this process; the rest are not wired.
            "binance": "CONFIGURED",
            "bybit": "CONFIGURED" if os.getenv("BYBIT_API_KEY") else "NOT_CONFIGURED",
            "okx": "CONFIGURED" if os.getenv("OKX_API_KEY") else "NOT_CONFIGURED",
            "coinbase": "NOT_CONFIGURED",
        },
        "evidence_class": "configuration_only",
        "note": "Reachability is not probed here; see /api/v1/health/detailed for measured pillars.",
        "observed_at": _utc_now_iso(),
    }
    return jsonify(format_api_response(integrations))
