"""
testnet_advisory_scheduler.py — Autonomous AI Advisory Scheduler for Binance Testnet/Futures Engine.

Features:
1. Gated by TESTNET_ADVISORY_ENABLED (default: False). If disabled, runs no background threads or consultations.
2. Controlled by TESTNET_ADVISORY_SHADOW_MODE (default: True).
   - Only applies recommendations to live runtime overlay when TESTNET_ADVISORY_ENABLED is True AND TESTNET_ADVISORY_SHADOW_MODE is False.
3. Strict Safety Invariants:
   - Maximum drawdown circuit breaker (TESTNET_ADVISORY_MAX_DRAWDOWN_PCT, default 15%). If hit, disables advisory and rolls back overlay to baseline defaults.
   - Position sizing clamped between 0.5x and 1.5x of current value.
   - Leverage can only decrease or stay unchanged.
   - Maximum ±20.0% parameter change limit.
   - Forbidden parameter list enforced (risk_limits, max_daily_loss, live_trading_enabled, api_key, etc.).
4. Consultation Triggers:
   - Scheduled: Every ADVISORY_INTERVAL_HOURS (default: 4 hours).
   - Event-driven: Drawdown threshold breached or consecutive loss streak >= 5.
   - Manual trigger: via API endpoint POST /api/testnet/advisory/trigger.
"""

import datetime
import os
import threading
from typing import Any

import config
from advisory_gate import AdvisoryGate
from advisory_ledger import append_advisory_entry
from advisory_params import AdvisoryParameterOverlay, get_advisory_overlay
from advisory_telemetry import build_telemetry_payload
from ai_universe_client import AIUniverseClient
from logger import get_logger

logger = get_logger("testnet_advisory_scheduler")


class TestnetAdvisoryScheduler:
    """
    Manages AI-Universe advisory consultations specifically for the Testnet execution engine.
    """
    __test__ = False

    def __init__(
        self,
        client: AIUniverseClient | None = None,
        gate: AdvisoryGate | None = None,
        overlay: AdvisoryParameterOverlay | None = None,
        enabled: bool | None = None,
        shadow_mode: bool | None = None,
        interval_hours: float | None = None,
        max_drawdown_pct: float | None = None
    ) -> None:
        self.enabled = (
            enabled if enabled is not None
            else getattr(config, "TESTNET_ADVISORY_ENABLED", os.getenv("TESTNET_ADVISORY_ENABLED", "False").lower() == "true")
        )
        self.shadow_mode = (
            shadow_mode if shadow_mode is not None
            else getattr(config, "TESTNET_ADVISORY_SHADOW_MODE", os.getenv("TESTNET_ADVISORY_SHADOW_MODE", "True").lower() == "true")
        )
        self.interval_hours = float(
            interval_hours if interval_hours is not None
            else getattr(config, "ADVISORY_INTERVAL_HOURS", os.getenv("ADVISORY_INTERVAL_HOURS", "4.0"))
        )
        self.max_drawdown_limit_pct = float(
            max_drawdown_pct if max_drawdown_pct is not None
            else getattr(config, "TESTNET_ADVISORY_MAX_DRAWDOWN_PCT", os.getenv("TESTNET_ADVISORY_MAX_DRAWDOWN_PCT", "0.15"))
        )

        base_url = getattr(config, "AI_UNIVERSE_BASE_URL", os.getenv("AI_UNIVERSE_BASE_URL", "http://localhost:8000"))
        timeout = int(getattr(config, "ADVISORY_TIMEOUT_SECONDS", os.getenv("ADVISORY_TIMEOUT_SECONDS", "120")))
        api_key = getattr(config, "AI_UNIVERSE_API_KEY", os.getenv("AI_UNIVERSE_API_KEY", ""))

        self.client = client or AIUniverseClient(base_url=base_url, timeout=timeout, api_key=api_key)
        self.gate = gate or AdvisoryGate()
        self.overlay = overlay or get_advisory_overlay()

        self._running = False
        self._circuit_broken = False
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._last_consultation_time: datetime.datetime | None = None
        self._last_applied_decision_ids: list[str] = []
        self._lock = threading.Lock()

    def check_drawdown_circuit_breaker(self, current_dd_pct: float) -> bool:
        """
        If current testnet drawdown exceeds the max limit (e.g. 15%), disables advisory
        and rolls back any active overlay overrides to clean baseline defaults.
        """
        threshold = self.max_drawdown_limit_pct * 100.0 if self.max_drawdown_limit_pct <= 1.0 else self.max_drawdown_limit_pct
        if current_dd_pct >= threshold and not self._circuit_broken:
            with self._lock:
                self._circuit_broken = True
                self.shadow_mode = True  # Force back to shadow mode
                logger.critical(
                    f"[TESTNET_ADVISORY] 🚨 CIRCUIT BREAKER TRIPPED: Drawdown ({current_dd_pct:.2f}%) exceeded limit ({threshold:.1f}%). "
                    f"Disabling live AI advisory and reverting overlay to baseline defaults."
                )
                # Revert all active overrides back to clean baseline defaults
                self.overlay.reset_to_defaults(reason=f"DRAWDOWN_{current_dd_pct:.1f}_EXCEEDED_{threshold:.1f}")
                self._last_applied_decision_ids.clear()
            return True
        return self._circuit_broken

    def run_consultation_cycle(self, reason: str = "SCHEDULED") -> dict[str, Any] | None:
        """
        Executes a single testnet advisory consultation cycle safely.
        """
        if not self.enabled:
            logger.info("[TESTNET_ADVISORY] Consultation skipped (TESTNET_ADVISORY_ENABLED=False).")
            return None

        try:
            logger.info(f"[TESTNET_ADVISORY] Starting consultation cycle (reason='{reason}', shadow_mode={self.shadow_mode})...")

            # 1. Build Testnet Telemetry
            trading_mode = getattr(config, "TRADING_MODE", "TESTNET").upper()
            telemetry = build_telemetry_payload(
                trading_mode=trading_mode,
                consultation_reason=reason
            )

            # Check drawdown limit before consulting
            curr_dd = float(telemetry.get("portfolio", {}).get("max_drawdown_pct", 0.0))
            if self.check_drawdown_circuit_breaker(curr_dd):
                logger.warning("[TESTNET_ADVISORY] Circuit breaker is active. Consultation aborted.")
                return None

            # 2. Consult AI-Universe
            decision = self.client.consult(telemetry)
            if not decision:
                logger.warning("[TESTNET_ADVISORY] AI-Universe returned no decision. Retaining last validated parameters.")
                return None

            # 3. Validate against AdvisoryGate bounds
            active_strat = telemetry.get("active_strategy", "aggressive_scalper")
            current_params = self.overlay.get_current_params(active_strat)
            last_applied_time = self.overlay._last_applied_time

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
                "trading_mode": trading_mode,
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
            append_advisory_entry(ledger_entry)

            # 5. Apply Changes if Validated and NOT in Shadow Mode
            if not self.shadow_mode and result.verdict == "APPLY":
                self.overlay.apply_changes(
                    decision_id=result.decision_id,
                    changes=result.applied_changes
                )
                with self._lock:
                    self._last_applied_decision_ids.append(result.decision_id)
                logger.info(f"[TESTNET_ADVISORY] Applied {len(result.applied_changes)} parameter changes to Testnet overlay.")
            else:
                logger.info(f"[TESTNET_ADVISORY] Verdict: {result.verdict} ({result.rationale}). No live parameter changes applied.")

            with self._lock:
                self._last_consultation_time = datetime.datetime.utcnow()

            return ledger_entry

        except Exception as e:
            logger.error(f"[TESTNET_ADVISORY] Error during testnet consultation cycle: {e}", exc_info=True)
            return None

    def trigger_manual_consultation(self) -> dict[str, Any] | None:
        """API hook for immediate manual consultation."""
        return self.run_consultation_cycle(reason="MANUAL_API_TRIGGER")

    def toggle_mode(self, shadow_mode: bool) -> bool:
        """Toggles between SHADOW and APPLY modes."""
        with self._lock:
            if not shadow_mode and self._circuit_broken:
                logger.warning("[TESTNET_ADVISORY] Cannot enable APPLY mode while circuit breaker is active.")
                return False
            self.shadow_mode = shadow_mode
            logger.info(f"[TESTNET_ADVISORY] Shadow mode toggled to: {self.shadow_mode}")
            return True

    def _worker_loop(self) -> None:
        """Background thread worker loop."""
        logger.info(f"[TESTNET_ADVISORY] Background worker thread started (interval={self.interval_hours}h).")
        check_interval_sec = 60

        while not self._stop_event.is_set():
            try:
                if self.enabled:
                    now = datetime.datetime.utcnow()
                    should_run = False
                    reason = "SCHEDULED"

                    if self._last_consultation_time is None:
                        should_run = True
                        reason = "STARTUP_SCHEDULED"
                    else:
                        elapsed_hours = (now - self._last_consultation_time).total_seconds() / 3600.0
                        if elapsed_hours >= self.interval_hours:
                            should_run = True
                            reason = f"PERIODIC_SCHEDULED ({self.interval_hours}h)"

                    if should_run:
                        self.run_consultation_cycle(reason=reason)

            except Exception as e:
                logger.error(f"[TESTNET_ADVISORY] Unexpected error in worker loop: {e}")

            self._stop_event.wait(timeout=check_interval_sec)

        logger.info("[TESTNET_ADVISORY] Worker thread stopped.")

    def start(self) -> None:
        """Starts background thread if enabled."""
        if not self.enabled:
            logger.info("[TESTNET_ADVISORY] Not starting worker thread (TESTNET_ADVISORY_ENABLED=False).")
            return

        with self._lock:
            if self._running:
                return
            self._stop_event.clear()
            self._running = True
            self._thread = threading.Thread(target=self._worker_loop, daemon=True, name="TestnetAdvisoryThread")
            self._thread.start()
            logger.info("[TESTNET_ADVISORY] Testnet Advisory background service started.")

    def stop(self) -> None:
        with self._lock:
            if not self._running:
                return
            self._stop_event.set()
            self._running = False
            if self._thread and self._thread.is_alive():
                self._thread.join(timeout=2.0)
            logger.info("[TESTNET_ADVISORY] Testnet Advisory background service stopped.")

    def get_status(self) -> dict[str, Any]:
        """Returns comprehensive status dictionary for API and dashboard."""
        mode_str = "DISABLED" if not self.enabled else ("SHADOW" if self.shadow_mode else "APPLY")
        with self._lock:
            return {
                "enabled": self.enabled,
                "shadow_mode": self.shadow_mode,
                "mode": mode_str,
                "circuit_broken": self._circuit_broken,
                "max_drawdown_limit_pct": self.max_drawdown_limit_pct * 100 if self.max_drawdown_limit_pct <= 1.0 else self.max_drawdown_limit_pct,
                "last_consultation_time": self._last_consultation_time.isoformat() + "Z" if self._last_consultation_time else None,
                "interval_hours": self.interval_hours,
                "applied_decisions_count": len(self._last_applied_decision_ids),
                "active_overrides": self.overlay.get_state().get("active_overrides", {})
            }


# Module singleton
_testnet_advisory_instance: TestnetAdvisoryScheduler | None = None
_testnet_advisory_lock = threading.Lock()


def get_testnet_advisory_scheduler() -> TestnetAdvisoryScheduler:
    global _testnet_advisory_instance
    if _testnet_advisory_instance is None:
        with _testnet_advisory_lock:
            if _testnet_advisory_instance is None:
                _testnet_advisory_instance = TestnetAdvisoryScheduler()
    return _testnet_advisory_instance


def start_testnet_advisory_if_enabled() -> TestnetAdvisoryScheduler | None:
    scheduler = get_testnet_advisory_scheduler()
    if scheduler.enabled:
        scheduler.start()
        return scheduler
    return None
