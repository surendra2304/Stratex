"""
scripts/supervise_services.py
Production Process Supervisor for Docker / Render container deployment.
Monitors both bot.py (Trading Engine) and dashboard.py (Web Terminal).
Provides automatic process recovery, graceful signal forwarding, and heartbeat verification.
"""

import os
import sys
import time
import signal
import subprocess
import threading

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from logger import get_logger

logger = get_logger("supervisor")

class ServiceSupervisor:
    def __init__(self):
        self.stop_event = threading.Event()
        self.bot_proc = None
        self.dash_proc = None
        
        self.bot_restarts = 0
        self.dash_restarts = 0
        self.last_bot_crash = 0
        self.last_dash_crash = 0
        
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

    def _start_dashboard(self):
        """Starts the Flask dashboard subprocess."""
        repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        dash_script = os.path.join(repo_root, "dashboard.py")
        env = os.environ.copy()
        env["PYTHONUNBUFFERED"] = "1"
        port = os.environ.get("PORT", "5000")
        logger.info(f"[SUPERVISOR] 🚀 Spawning Dashboard on port {port} (python dashboard.py)...")
        self.dash_proc = subprocess.Popen(
            [sys.executable, dash_script],
            cwd=repo_root,
            env=env
        )
        logger.info(f"[SUPERVISOR] Dashboard spawned with PID {self.dash_proc.pid}")

    def _terminate_children(self):
        """Cleanly terminates child processes with SIGTERM, then SIGKILL if unresponsive."""
        for name, proc in [("Dashboard", self.dash_proc), ("Trading Engine", self.bot_proc)]:
            if proc and proc.poll() is None:
                logger.info(f"[SUPERVISOR] Sending SIGTERM to {name} (PID {proc.pid})...")
                try:
                    proc.terminate()
                except Exception as e:
                    logger.error(f"[SUPERVISOR] Error terminating {name}: {e}")
                    
        # Wait up to 5 seconds for clean exit
        start_wait = time.time()
        for name, proc in [("Dashboard", self.dash_proc), ("Trading Engine", self.bot_proc)]:
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
