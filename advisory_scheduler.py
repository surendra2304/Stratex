"""
advisory_scheduler.py — Autonomous background scheduler and event-driven trigger for AI consultations.

Runs in a daemon background thread alongside both bot.py (Testnet) and paper_forward_runner.py (Paper).
Execution Triggers:
1. Scheduled: Every ADVISORY_INTERVAL_HOURS (default 4 hours).
2. Event-Driven: Drawdown exceeds threshold OR consecutive loss streak >= 5.

Flow:
build_telemetry -> client.consult -> advisory_gate.validate -> append advisory_log
-> IF shadow_mode is False AND verdict is APPLY: apply parameter changes to runtime overlay.

ZERO DOWNTIME TOLERANCE: All exceptions are caught, logged, and NEVER propagate or block the trading engine.
"""

import datetime
import os
import threading
import time
from typing import Any, Dict, Optional

from advisory_gate import AdvisoryGate
from advisory_ledger import append_advisory_entry
from advisory_params import get_advisory_overlay
from advisory_telemetry import build_telemetry_payload
from ai_universe_client import AIUniverseClient
import config
from logger import get_logger

logger = get_logger("advisory_scheduler")


class AdvisoryScheduler:
    """
    Background worker orchestrating AI-Universe consultations and safety evaluations.
    """

    def __init__(
        self,
        client: Optional[AIUniverseClient] = None,
        gate: Optional[AdvisoryGate] = None,
        trading_mode: Optional[str] = None,
        shadow_mode: Optional[bool] = None,
        interval_hours: Optional[float] = None
    ) -> None:
        base_url = getattr(config, "AI_UNIVERSE_BASE_URL", os.getenv("AI_UNIVERSE_BASE_URL", "http://localhost:8000"))
        timeout = int(getattr(config, "ADVISORY_TIMEOUT_SECONDS", os.getenv("ADVISORY_TIMEOUT_SECONDS", "120")))
        api_key = getattr(config, "AI_UNIVERSE_API_KEY", os.getenv("AI_UNIVERSE_API_KEY", os.getenv("FRIDAY_UNIVERSE_API_KEY", "")))

        self.client = client or AIUniverseClient(base_url=base_url, timeout=timeout, api_key=api_key)
        self.gate = gate or AdvisoryGate()
        self.trading_mode = (trading_mode or getattr(config, "TRADING_MODE", "PAPER")).upper()
        
        self.shadow_mode = (
            shadow_mode if shadow_mode is not None
            else getattr(config, "ADVISORY_SHADOW_MODE", os.getenv("ADVISORY_SHADOW_MODE", "True").lower() == "true")
        )
        self.interval_hours = float(
            interval_hours if interval_hours is not None
            else getattr(config, "ADVISORY_INTERVAL_HOURS", os.getenv("ADVISORY_INTERVAL_HOURS", "4.0"))
        )

        self._running = False
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._last_consultation_time: Optional[datetime.datetime] = None
        self._last_consecutive_losses_alerted: int = 0
        self._lock = threading.Lock()

    def run_consultation_cycle(self, reason: str = "SCHEDULED") -> Optional[Dict[str, Any]]:
        """
        Executes one full consultation cycle safely. Returns consultation result dict or None on failure.
        """
        try:
            logger.info(f"[ADVISORY_SCHEDULER] Starting consultation cycle (reason='{reason}', shadow_mode={self.shadow_mode})...")

            # 1. Build Telemetry Payload
            telemetry = build_telemetry_payload(
                trading_mode=self.trading_mode,
                consultation_reason=reason
            )

            # 2. Consult AI-Universe
            decision = self.client.consult(telemetry)
            if not decision:
                logger.warning(f"[ADVISORY_SCHEDULER] AI-Universe returned no decision. Retaining last validated parameters.")
                return None

            # 3. Validate against AdvisoryGate bounds
            overlay = get_advisory_overlay()
            active_strat = telemetry.get("active_strategy", "aggressive_scalper")
            current_params = overlay.get_current_params(active_strat)
            last_applied_time = overlay._last_applied_time

            result = self.gate.validate(
                decision=decision,
                current_params=current_params,
                last_applied_time=last_applied_time,
                shadow_mode=self.shadow_mode
            )

            # 4. Append to Advisory Audit Ledger
            ledger_entry = {
                "timestamp": datetime.datetime.utcnow().isoformat() + "Z",
                "decision_id": result.decision_id,
                "consultation_reason": reason,
                "ai_status": decision.get("status", "UNKNOWN"),
                "confidence": decision.get("confidence", 0.0),
                "requested_changes": decision.get("parameter_changes", []),
                "verdict": result.verdict,
                "applied_changes": result.applied_changes,
                "rejected_changes": result.rejected_changes,
                "ai_debate_summary": decision.get("debate_summary", decision.get("recommendation", "")),
                "regime_analysis": telemetry.get("market_regime", {}),
                "latency_ms": decision.get("latency_ms", 0.0),
                "shadow_mode": self.shadow_mode,
                "bounds_checked": result.bounds_checked
            }
            appended = append_advisory_entry(ledger_entry)
            if not appended:
                logger.critical("[ADVISORY_SCHEDULER] 🚨 CRITICAL: Advisory ledger write failed (possible disk failure). Disabling advisory subsystem.")
                self.stop()
                return None

            # 5. Apply Changes if Validated and NOT in Shadow Mode
            if not self.shadow_mode and result.verdict == "APPLY":
                overlay.apply_changes(
                    decision_id=result.decision_id,
                    changes=result.applied_changes
                )
                logger.info(f"[ADVISORY_SCHEDULER] Applied {len(result.applied_changes)} parameter changes from decision {result.decision_id}.")
            else:
                logger.info(f"[ADVISORY_SCHEDULER] Verdict: {result.verdict} ({result.rationale}). No live parameter changes applied.")

            with self._lock:
                self._last_consultation_time = datetime.datetime.utcnow()

            return ledger_entry

        except Exception as e:
            logger.error(f"[ADVISORY_SCHEDULER] Error during consultation cycle: {e}", exc_info=True)
            return None

    def _should_trigger_event(self) -> Optional[str]:
        """
        Checks if event-based consultation trigger conditions are met:
        - Drawdown exceeds safety threshold.
        - Consecutive losses >= 5.
        """
        try:
            telemetry = build_telemetry_payload(trading_mode=self.trading_mode)
            perf = telemetry.get("performance_metrics", {})
            port = telemetry.get("portfolio", {})

            losses = perf.get("consecutive_losses", 0)
            if losses >= 5 and losses > self._last_consecutive_losses_alerted:
                self._last_consecutive_losses_alerted = losses
                return f"LOSS_STREAK_TRIGGER (consecutive_losses={losses})"

            # Max drawdown check
            dd_pct = port.get("max_drawdown_pct", 0.0)
            threshold = float(getattr(config, "MAX_TESTNET_DRAWDOWN_PCT", 0.05)) * 100.0
            if dd_pct > threshold and threshold > 0:
                return f"DRAWDOWN_BREACH_TRIGGER (dd={dd_pct:.1f}% > {threshold:.1f}%)"

        except Exception as e:
            logger.warning(f"[ADVISORY_SCHEDULER] Error checking event triggers: {e}")

        return None

    def _worker_loop(self) -> None:
        """Main loop running inside the background daemon thread."""
        logger.info(f"[ADVISORY_SCHEDULER] Background worker thread started (interval={self.interval_hours}h).")
        
        # Initial scheduled run check
        check_interval_sec = 60  # Poll every 60 seconds

        while not self._stop_event.is_set():
            try:
                now = datetime.datetime.utcnow()
                should_run = False
                reason = "SCHEDULED"

                # Check 1: Scheduled timer
                if self._last_consultation_time is None:
                    should_run = True
                    reason = "STARTUP_SCHEDULED"
                else:
                    elapsed_hours = (now - self._last_consultation_time).total_seconds() / 3600.0
                    if elapsed_hours >= self.interval_hours:
                        should_run = True
                        reason = f"PERIODIC_SCHEDULED ({self.interval_hours}h)"

                # Check 2: Event-driven trigger
                if not should_run:
                    event_reason = self._should_trigger_event()
                    if event_reason:
                        should_run = True
                        reason = event_reason

                if should_run:
                    self.run_consultation_cycle(reason=reason)

            except Exception as e:
                logger.error(f"[ADVISORY_SCHEDULER] Unexpected error in worker loop: {e}")

            # Sleep with interruptibility
            self._stop_event.wait(timeout=check_interval_sec)

        logger.info("[ADVISORY_SCHEDULER] Worker thread terminated cleanly.")

    def start(self) -> None:
        """Starts the background scheduler thread."""
        with self._lock:
            if self._running:
                return
            self._stop_event.clear()
            self._running = True
            self._thread = threading.Thread(target=self._worker_loop, daemon=True, name="AdvisorySchedulerThread")
            self._thread.start()
            logger.info("[ADVISORY_SCHEDULER] Advisory background service started.")

    def stop(self) -> None:
        """Signals the background scheduler thread to terminate."""
        with self._lock:
            if not self._running:
                return
            self._stop_event.set()
            self._running = False
            if self._thread and self._thread.is_alive():
                self._thread.join(timeout=2.0)
            logger.info("[ADVISORY_SCHEDULER] Advisory background service stopped.")


# Singleton supervisor instance
_scheduler_instance: Optional[AdvisoryScheduler] = None
_scheduler_lock = threading.Lock()


def get_advisory_scheduler() -> AdvisoryScheduler:
    """Returns the singleton instance of AdvisoryScheduler."""
    global _scheduler_instance
    if _scheduler_instance is None:
        with _scheduler_lock:
            if _scheduler_instance is None:
                _scheduler_instance = AdvisoryScheduler()
    return _scheduler_instance


def start_advisory_scheduler_if_enabled() -> Optional[AdvisoryScheduler]:
    """Helper to start the scheduler if AI_UNIVERSE_ENABLED is True."""
    enabled = getattr(config, "AI_UNIVERSE_ENABLED", os.getenv("AI_UNIVERSE_ENABLED", "True").lower() == "true")
    if enabled:
        scheduler = get_advisory_scheduler()
        scheduler.start()
        return scheduler
    return None
