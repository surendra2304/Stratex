"""
risk/drawdown_controller.py — Progressive Drawdown Controller & Recovery Protocol.

Monitors real-time equity drawdowns with multi-stage levels:
1. WARNING LEVEL (5-8% DD): Reduces all new position sizing by 30%.
2. ACTION LEVEL (8-12% DD): Reduces open positions by 50% and halts all new trade entries.
3. CRITICAL LEVEL (12-15% DD): Flattens all positions, executes full system halt, and alerts operator.

Recovery Protocol:
- After a CRITICAL event, requires 48 hours of clean paper trading before testnet resumption.
- Progressive Re-Entry: Starts at 25% normal sizing, increasing by 25% per clean trading week.

Fail-closed numeric handling (all previously fail-open):
- A NaN equity reading evaluated as NOMINAL (``NaN >= threshold`` is False);
  it is now a CRITICAL breach.
- A single ``inf`` reading became the peak forever, so every later drawdown
  was NaN → NOMINAL; non-finite readings never update the peak now.
- Any equity bounce after a CRITICAL event re-enabled entries at full size
  while ``in_recovery_mode`` was still True, bypassing the 48h paper
  validation; recovery now holds entries at zero until validation completes.
- ``progress_recovery_paper_trading(inf)`` completed validation instantly.
- NaN thresholds disabled the levels; thresholds are validated at init.
"""

import time
from dataclasses import dataclass
from typing import Any

from logger import get_logger
from numeric_safety import finite_float, positive_float

logger = get_logger("drawdown_controller")

RECOVERY_LEVEL = "RECOVERY_PAPER_VALIDATION"


@dataclass
class DrawdownStatus:
    peak_equity: float
    current_equity: float
    drawdown_pct: float
    level: str  # "NOMINAL", "WARNING_5PCT", "ACTION_8PCT", "CRITICAL_12PCT", RECOVERY_LEVEL
    position_size_multiplier: float = 1.0
    allow_new_entries: bool = True
    in_recovery_mode: bool = False
    clean_paper_hours_accumulated: float = 0.0
    progressive_reentry_tier: float = 1.0  # 0.25 -> 0.50 -> 0.75 -> 1.0

    # Backwards compatibility property
    @property
    def current_drawdown_pct(self) -> float:
        return self.drawdown_pct


def _threshold_pct(name: str, value) -> float:
    """Drawdown threshold in percent.

    Values below 1.0 are fractions (0.05 → 5%), values from 1.0 up are already
    percentages. Non-finite, non-positive or ≥100% thresholds are rejected
    instead of silently disabling the level.
    """
    number = finite_float(value)
    if number is None or number <= 0:
        raise ValueError(f"{name}={value!r} must be a finite number > 0")
    pct = number * 100.0 if number < 1.0 else number
    if pct >= 100.0:
        raise ValueError(f"{name}={value!r} must be below 100%")
    return pct


class DrawdownController:
    """
    Supervises equity drawdowns and enforces multi-stage protective actions and progressive recovery.
    """

    def __init__(
        self,
        initial_equity: float = 5000.0,
        warning_drawdown_pct: float = 0.05,
        critical_drawdown_pct: float = 0.12,
        initial_capital: float | None = None
    ):
        raw_start = initial_capital if initial_capital is not None else initial_equity
        start_cap = positive_float(raw_start)
        if start_cap is None:
            raise ValueError(f"initial equity must be a finite number > 0, got {raw_start!r}")
        self.peak_equity = start_cap
        self.current_equity = start_cap
        self.warning_threshold_pct = _threshold_pct("warning_drawdown_pct", warning_drawdown_pct)
        self.critical_threshold_pct = _threshold_pct("critical_drawdown_pct", critical_drawdown_pct)
        if not self.warning_threshold_pct < self.critical_threshold_pct:
            raise ValueError(
                f"warning threshold ({self.warning_threshold_pct}%) must be below "
                f"critical threshold ({self.critical_threshold_pct}%)"
            )

        self.status = DrawdownStatus(
            peak_equity=start_cap,
            current_equity=start_cap,
            drawdown_pct=0.0,
            level="NOMINAL"
        )
        self.last_critical_event_time: float | None = None

    def _enter_critical(self) -> None:
        self.status.level = "CRITICAL_12PCT"
        self.status.position_size_multiplier = 0.0
        self.status.allow_new_entries = False
        if not self.status.in_recovery_mode:
            self.status.clean_paper_hours_accumulated = 0.0
        self.status.in_recovery_mode = True
        if self.last_critical_event_time is None:
            self.last_critical_event_time = time.time()

    def update_equity(self, current_equity: float) -> DrawdownStatus:
        """Calculates current peak-to-trough drawdown and assigns risk level."""
        equity = finite_float(current_equity)
        if equity is None:
            logger.critical(f"[DRAWDOWN_CTRL] 🚨 Non-finite equity reading {current_equity!r}: treating as CRITICAL")
            self._enter_critical()
            return self.status

        self.current_equity = equity
        self.peak_equity = max(self.peak_equity, equity)

        # Divide by the real peak: the old max(peak, 1.0) understated the
        # drawdown of accounts smaller than one quote unit.
        dd_pct = ((self.peak_equity - equity) / self.peak_equity) * 100.0
        self.status.peak_equity = round(self.peak_equity, 2)
        self.status.current_equity = round(equity, 2)
        self.status.drawdown_pct = round(dd_pct, 2)

        # Evaluate Levels
        if dd_pct >= self.critical_threshold_pct:
            self._enter_critical()
            logger.critical(f"[DRAWDOWN_CTRL] 🚨 CRITICAL DRAWDOWN ({dd_pct:.1f}% >= {self.critical_threshold_pct}%): FLATTEN AND HALT")
        elif dd_pct >= (self.warning_threshold_pct + (self.critical_threshold_pct - self.warning_threshold_pct) / 2):
            self.status.level = "ACTION_8PCT"
            self.status.position_size_multiplier = 0.50
            self.status.allow_new_entries = False
            logger.warning(f"[DRAWDOWN_CTRL] ⚠️ ACTION LEVEL DRAWDOWN ({dd_pct:.1f}%): Halt new entries, reduce sizes 50%")
        elif dd_pct >= self.warning_threshold_pct:
            self.status.level = "WARNING_5PCT"
            self.status.position_size_multiplier = 0.70
            self.status.allow_new_entries = True
            logger.info(f"[DRAWDOWN_CTRL] ℹ️ WARNING LEVEL DRAWDOWN ({dd_pct:.1f}% >= {self.warning_threshold_pct}%): Reduce sizes 30%")
        else:
            self.status.level = "NOMINAL"
            self.status.position_size_multiplier = self.status.progressive_reentry_tier
            self.status.allow_new_entries = True

        if self.status.in_recovery_mode and self.status.level != "CRITICAL_12PCT":
            # Equity recovered but the post-CRITICAL paper validation has not
            # completed: entries stay blocked.
            self.status.level = RECOVERY_LEVEL
            self.status.position_size_multiplier = 0.0
            self.status.allow_new_entries = False
        return self.status

    def get_defensive_action(self) -> dict[str, Any]:
        """Returns defensive action dict for backward compatibility with test_advanced_risk.py."""
        if self.status.in_recovery_mode or self.status.drawdown_pct >= self.critical_threshold_pct:
            return {"action": "HALT_AND_FLAT", "sizing_factor": 0.0}
        elif self.status.drawdown_pct >= self.warning_threshold_pct:
            return {"action": "THROTTLE_SIZING", "sizing_factor": 0.5}
        return {"action": "NONE", "sizing_factor": 1.0}

    def progress_recovery_paper_trading(self, hours_elapsed: float) -> tuple[bool, float]:
        """Tracks 48h mandatory paper validation before testnet resumption."""
        if not self.status.in_recovery_mode:
            return True, 1.0
        hours = finite_float(hours_elapsed)
        if hours is None or hours < 0 or hours > 24 * 7:
            raise ValueError(f"hours_elapsed must be within [0, 168], got {hours_elapsed!r}")

        self.status.clean_paper_hours_accumulated += hours
        if self.status.clean_paper_hours_accumulated >= 48.0:
            self.status.in_recovery_mode = False
            self.status.progressive_reentry_tier = 0.25  # Start with 25% sizing
            self.status.position_size_multiplier = 0.25
            self.status.allow_new_entries = True
            self.status.level = "NOMINAL"
            self.last_critical_event_time = None
            logger.info("[DRAWDOWN_CTRL] 🟢 48h clean paper validation complete. Resuming with 25% sizing tier.")
            return True, 0.25

        return False, 0.0

    def advance_progressive_reentry(self) -> float:
        """Increases sizing by +25% after a clean trading week (not during recovery)."""
        if self.status.in_recovery_mode:
            logger.warning("[DRAWDOWN_CTRL] Re-entry tier not advanced: paper validation still pending")
            return self.status.progressive_reentry_tier
        self.status.progressive_reentry_tier = min(1.0, self.status.progressive_reentry_tier + 0.25)
        self.status.position_size_multiplier = self.status.progressive_reentry_tier
        logger.info(f"[DRAWDOWN_CTRL] 📈 Advanced progressive re-entry tier to {int(self.status.progressive_reentry_tier*100)}%")
        return self.status.progressive_reentry_tier
