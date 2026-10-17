"""
scripts/supervise_services.py
Production Process Supervisor for Docker / Render container deployment.
Monitors both bot.py (Trading Engine) and dashboard.py (Web Terminal).
Provides automatic process recovery, graceful signal forwarding, and heartbeat verification.
"""

import os
import signal
import subprocess
import sys
import threading
import time

repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if repo_root not in sys.path:
    sys.path.insert(0, repo_root)

from logger import get_logger

logger = get_logger("supervisor")

class ServiceSupervisor:
    def __init__(self):
        self.stop_event = threading.Event()
        self.bot_proc = None
        self.dash_proc = None
        self.reconciliation_proc = None

        self.bot_restarts = 0
        self.dash_restarts = 0
        self.reconciliation_restarts = 0
        self.last_bot_crash = 0
        self.last_dash_crash = 0
        self.last_reconciliation_crash = 0
        
        # Setup signal handlers
        signal.signal(signal.SIGTERM, self._handle_signal)
        signal.signal(signal.SIGINT, self._handle_signal)
        if hasattr(signal, "SIGHUP"):
            signal.signal(signal.SIGHUP, self._handle_signal)

    def _handle_signal(self, signum, frame):
        logger.info(f"[SUPERVISOR] Received signal {signum}. Initiating graceful container shutdown...")
        self.stop_event.set()
        self._terminate_children()
        sys.exit(0)

    def _start_bot(self):
        """Starts the trading bot daemon subprocess."""
        repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        bot_script = os.path.join(repo_root, "bot.py")
        env = os.environ.copy()
        env["PYTHONUNBUFFERED"] = "1"
        logger.info("[SUPERVISOR] 🚀 Spawning Trading Engine (python bot.py)...")
        self.bot_proc = subprocess.Popen(
            [sys.executable, bot_script],
            cwd=repo_root,
            env=env
        )
        logger.info(f"[SUPERVISOR] Trading Engine spawned with PID {self.bot_proc.pid}")

    @staticmethod
    def _truthy_env(name):
        return os.getenv(name, "false").strip().lower() in {"1", "true", "yes", "on"}

    def _start_reconciliation_worker(self):
        """Launch only when both opt-in flags and the demo-only runtime gates are valid."""
        if not (
            self._truthy_env("STRATEX_ORDER_MANAGER_ENABLED")
            and self._truthy_env("STRATEX_RECONCILIATION_ENABLED")
        ):
            logger.info(
                "[SUPERVISOR] Reconciliation worker disabled "
                "(requires STRATEX_ORDER_MANAGER_ENABLED=true and STRATEX_RECONCILIATION_ENABLED=true)."
            )
            return

        trading_mode = os.getenv("TRADING_MODE", "PAPER").strip().upper()
        execution_mode = os.getenv("STRATEX_EXECUTION_MODE", "DISABLED").strip().upper()
        if self._truthy_env("LIVE_TRADING_ENABLED") or trading_mode != "FUTURES" or execution_mode != "DEMO":
            logger.error(
                "[SUPERVISOR] Reconciliation worker requested but safety gates are not satisfied; not starting it."
            )
            return
        allowed = {
            item.strip().lower()
            for item in os.getenv("STRATEX_ALLOWED_EXECUTION_VENUES", "binance").split(",")
            if item.strip()
        }
        if not allowed or not allowed.issubset({"binance", "mt5"}):
            logger.error("[SUPERVISOR] Reconciliation worker has an invalid venue allowlist; not starting it.")
            return
        if "binance" in allowed and not self._truthy_env("TESTNET_ENABLED"):
            logger.error("[SUPERVISOR] Reconciliation worker needs TESTNET_ENABLED=true for Binance demo/testnet.")
            return
        if "mt5" in allowed and (
            not self._truthy_env("MT5_BRIDGE_ENABLED")
            or not self._truthy_env("MT5_REQUIRE_DEMO")
            or not os.getenv("MT5_BRIDGE_URL", "").strip()
            or not os.getenv("MT5_BRIDGE_TOKEN", "").strip()
        ):
            logger.error("[SUPERVISOR] Reconciliation worker needs an explicitly enabled, configured MT5 demo bridge.")
            return

        worker_script = os.path.join(repo_root, "scripts", "reconciliation_worker.py")
        env = os.environ.copy()
        env["PYTHONUNBUFFERED"] = "1"
        logger.info("[SUPERVISOR] Starting read-only OMS reconciliation worker.")
        self.reconciliation_proc = subprocess.Popen(
            [sys.executable, worker_script],
            cwd=repo_root,
            env=env,
        )
        logger.info(
            "[SUPERVISOR] Reconciliation worker spawned with PID %s",
            self.reconciliation_proc.pid,
        )

    def _start_dashboard(self):
        """Starts the dashboard subprocess using Gunicorn WSGI server."""
        repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        env = os.environ.copy()
        env["PYTHONUNBUFFERED"] = "1"
        port = os.environ.get("PORT", "8000")
        env["PORT"] = port

        # Check if gunicorn is available
        try:
            import gunicorn
            has_gunicorn = True
        except ImportError:
            has_gunicorn = False

        if has_gunicorn:
            # Multi-threaded production WSGI server with unlimited timeout for SSE streaming (/api/stream)
            cmd = [
                sys.executable, "-m", "gunicorn",
                "--bind", f"0.0.0.0:{port}",
                "--workers", "2",
                "--threads", "4",
                "--timeout", "0",
                "dashboard:app"
            ]
            logger.info(f"[SUPERVISOR] 🚀 Spawning Dashboard on port {port} with Gunicorn WSGI ({' '.join(cmd)})...")
        else:
            dash_script = os.path.join(repo_root, "dashboard.py")
            cmd = [sys.executable, dash_script]
            logger.info(f"[SUPERVISOR] 🚀 Spawning Dashboard on port {port} (fallback: python dashboard.py)...")

        self.dash_proc = subprocess.Popen(
            cmd,
            cwd=repo_root,
            env=env
        )
        logger.info(f"[SUPERVISOR] Dashboard spawned with PID {self.dash_proc.pid}")

    def _terminate_children(self):
        """Cleanly terminates child processes with SIGTERM, then SIGKILL if unresponsive."""
        for name, proc in [
            ("Dashboard", self.dash_proc),
            ("Trading Engine", self.bot_proc),
            ("OMS Reconciliation", self.reconciliation_proc),
        ]:
            if proc and proc.poll() is None:
                logger.info(f"[SUPERVISOR] Sending SIGTERM to {name} (PID {proc.pid})...")
                try:
                    proc.terminate()
                except Exception as e:
                    logger.error(f"[SUPERVISOR] Error terminating {name}: {e}")
                    
        # Wait up to 5 seconds for clean exit
        start_wait = time.time()
        for name, proc in [
            ("Dashboard", self.dash_proc),
            ("Trading Engine", self.bot_proc),
            ("OMS Reconciliation", self.reconciliation_proc),
        ]:
            if proc and proc.poll() is None:
                try:
                    remaining = max(0.1, 5.0 - (time.time() - start_wait))
                    proc.wait(timeout=remaining)
                except subprocess.TimeoutExpired:
                    logger.warning(f"[SUPERVISOR] ⚠️ {name} did not exit in time. Forcing SIGKILL...")
                    try:
                        proc.kill()
                    except Exception as e:
                        logger.error(f"[SUPERVISOR] Error killing {name}: {e}")

    def run(self):
        print("=" * 65)
        print("  PRODUCTION PROCESS SUPERVISOR (RENDER / DOCKER DEPLOYMENT)")
        print("  Services: [1] Trading Engine (bot.py) | [2] Dashboard (dashboard.py)")
        print(f"  Supervisor PID: {os.getpid()} | Python: {sys.version.split()[0]}")
        print("=" * 65)
        
        self._start_bot()
        time.sleep(1) # Brief pause before dashboard starts
        self._start_dashboard()
        self._start_reconciliation_worker()

        while not self.stop_event.is_set():
            try:
                time.sleep(1)
                
                # 1. Check Trading Engine (bot.py)
                bot_status = self.bot_proc.poll() if self.bot_proc else None
                if bot_status is not None and not self.stop_event.is_set():
                    logger.critical(f"[SUPERVISOR] 🚨 Trading Engine (bot.py) EXITED unexpectedly with code {bot_status}!")
                    self.bot_restarts += 1
                    now = time.time()
                    
                    # Backoff if crashing repeatedly
                    backoff = 3.0
                    if now - self.last_bot_crash < 10:
                        backoff = 6.0
                    self.last_bot_crash = now
                    
                    logger.info(f"[SUPERVISOR] 🔄 Auto-recovering Trading Engine in {backoff}s (Restart count: {self.bot_restarts})...")
                    time.sleep(backoff)
                    self._start_bot()
                    
                # 3. Check optional OMS reconciliation worker (read-only; never submits orders)
                reconciliation_status = (
                    self.reconciliation_proc.poll() if self.reconciliation_proc else None
                )
                if (
                    self.reconciliation_proc is not None
                    and reconciliation_status is not None
                    and not self.stop_event.is_set()
                ):
                    logger.error(
                        "[SUPERVISOR] OMS Reconciliation worker exited with code %s.",
                        reconciliation_status,
                    )
                    self.reconciliation_restarts += 1
                    now = time.time()
                    backoff = 5.0 if now - self.last_reconciliation_crash >= 10 else 15.0
                    self.last_reconciliation_crash = now
                    logger.info(
                        "[SUPERVISOR] Restarting OMS Reconciliation worker in %ss (restart count: %s).",
                        backoff, self.reconciliation_restarts,
                    )
                    self.stop_event.wait(backoff)
                    if not self.stop_event.is_set():
                        self._start_reconciliation_worker()

                # 2. Check Dashboard (dashboard.py)
                dash_status = self.dash_proc.poll() if self.dash_proc else None
                if dash_status is not None and not self.stop_event.is_set():
                    logger.critical(f"[SUPERVISOR] 🚨 Dashboard (dashboard.py) EXITED unexpectedly with code {dash_status}!")
                    self.dash_restarts += 1
                    now = time.time()
                    
                    backoff = 2.0
                    if now - self.last_dash_crash < 10:
                        backoff = 5.0
                    self.last_dash_crash = now
                    
                    logger.info(f"[SUPERVISOR] 🔄 Auto-recovering Dashboard in {backoff}s (Restart count: {self.dash_restarts})...")
                    time.sleep(backoff)
                    self._start_dashboard()
                    
            except (KeyboardInterrupt, SystemExit):
                break
            except Exception as e:
                logger.error(f"[SUPERVISOR] Loop error: {e}")
                
        self._terminate_children()
        logger.info("[SUPERVISOR] Supervisor stopped.")

if __name__ == "__main__":
    supervisor = ServiceSupervisor()
    supervisor.run()
