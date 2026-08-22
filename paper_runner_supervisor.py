"""
paper_runner_supervisor.py — Supervised background execution of the paper
forward runner (upgrade 3).

The paper forward experiment (forward_exp_001) stalled on 2026-08-18 because
the runner was a standalone script with no supervision. This module runs
paper_forward_runner.run() in a daemon thread, restarts it with capped
backoff if it dies, and writes a heartbeat file that the dashboard exposes
via /api/engine-health (paper_runner_status).

Isolation: pure additive module. The runner only touches paper_* files and
never places exchange orders. Disable with SUPERVISE_PAPER_RUNNER=0.
"""
import datetime
import json
import os
import threading
import time
import traceback

from logger import get_logger

logger = get_logger("paper_supervisor")

HEARTBEAT_FILE = os.getenv("PAPER_RUNNER_HEARTBEAT_FILE", "paper_runner_heartbeat.json")
MAX_BACKOFF_SECONDS = 300
HEARTBEAT_STALE_SECONDS = 180

_state = {
    "thread": None,
    "alive": False,
    "started_at": None,
    "restarts": 0,
    "last_error": None,
    "last_heartbeat_ts": None,
}
_lock = threading.Lock()


def _write_heartbeat(status, error=None):
    _state["last_heartbeat_ts"] = time.time()
    payload = {
        "status": status,
        "alive": _state["alive"],
        "pid": os.getpid(),
        "restarts": _state["restarts"],
        "started_at": _state["started_at"],
        "last_error": error or _state["last_error"],
        "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    }
    try:
        tmp = HEARTBEAT_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)
        os.replace(tmp, HEARTBEAT_FILE)
    except Exception as e:  # heartbeat must never crash supervision
        logger.error(f"[PAPER_SUPERVISOR] heartbeat write failed: {e}")


def _heartbeat_watchdog(stop_event):
    """Write a RUNNING heartbeat every 30s while the runner thread is alive.

    Without this, the heartbeat only updates on state transitions and a
    healthy runner in its 60s poll loop looks DEAD once the heartbeat goes
    stale (>180s) — a monitoring false-negative observed on Render.
    """
    while not stop_event.wait(30):
        if _state["alive"]:
            _write_heartbeat("RUNNING")


def _supervise():
    backoff = 5
    while True:
        try:
            _state["alive"] = True
            _write_heartbeat("RUNNING")
            logger.info("[PAPER_SUPERVISOR] Starting paper_forward_runner.run()")
            from paper_forward_runner import run as runner_run
            runner_run()  # blocks for the lifetime of the experiment
            # A clean return means the experiment ended (e.g., kill switch)
            logger.warning("[PAPER_SUPERVISOR] paper runner returned; restarting in case of restartable exit")
        except BaseException as e:  # incl. SystemExit — otherwise restarts never happen
            _state["last_error"] = f"{type(e).__name__}: {e}"
            logger.error(f"[PAPER_SUPERVISOR] paper runner DIED: {e}\n{traceback.format_exc()}")
        finally:
            _state["alive"] = False
            _write_heartbeat("DEAD", _state["last_error"])
        # Cap restarts storm: 5s,10s,20s,...300s
        time.sleep(backoff)
        backoff = min(backoff * 2, MAX_BACKOFF_SECONDS)
        with _lock:
            _state["restarts"] += 1


def start_supervised_runner(force=False):
    """Start the supervised paper-runner thread once. Idempotent."""
    if os.getenv("SUPERVISE_PAPER_RUNNER", "1") != "1" and not force:
        logger.info("[PAPER_SUPERVISOR] Disabled via SUPERVISE_PAPER_RUNNER=0")
        _write_heartbeat("DISABLED")
        return False
    if os.environ.get("PYTEST_CURRENT_TEST") and not force:
        return False
    with _lock:
        t = _state["thread"]
        if t is not None and t.is_alive():
            return True
        _state["started_at"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
        t = threading.Thread(target=_supervise, name="paper-runner-supervisor", daemon=True)
        _state["thread"] = t
        t.start()
        # Periodic heartbeat so a healthy polling runner is never marked DEAD by staleness
        if _state.get("watchdog_thread") is None or not _state["watchdog_thread"].is_alive():
            wd = threading.Thread(target=_heartbeat_watchdog,
                                  args=(threading.Event(),),
                                  name="paper-runner-heartbeat", daemon=True)
            _state["watchdog_thread"] = wd
            wd.start()
        logger.info("[PAPER_SUPERVISOR] Supervised paper runner thread started")
        return True


def get_status():
    """Current supervisor status for /api/engine-health."""
    if os.getenv("SUPERVISE_PAPER_RUNNER", "1") != "1":
        return {"paper_runner_status": "DISABLED", "restarts": 0}
    # Prefer the on-disk heartbeat (works across processes)
    try:
        with open(HEARTBEAT_FILE, "r", encoding="utf-8") as f:
            hb = json.load(f)
        age = time.time() - datetime.datetime.fromisoformat(
            hb["timestamp"].replace("Z", "+00:00")
        ).timestamp()
        status = hb.get("status", "UNKNOWN")
        if status == "RUNNING" and age > HEARTBEAT_STALE_SECONDS:
            status = "DEAD"  # heartbeat went stale — thread hung or killed
        return {
            "paper_runner_status": status,
            "paper_runner_restarts": hb.get("restarts", 0),
            "paper_runner_heartbeat_age_seconds": round(age, 1),
            "paper_runner_last_error": hb.get("last_error"),
        }
    except Exception:
        return {"paper_runner_status": "DEAD", "paper_runner_restarts": _state["restarts"]}
