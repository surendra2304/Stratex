"""
risk/drawdown_controller.py — Progressive Drawdown Controller & Recovery Protocol.

Monitors real-time equity drawdowns with multi-stage levels:
1. WARNING LEVEL (5-8% DD): Reduces all new position sizing by 30%.
2. ACTION LEVEL (8-12% DD): Reduces open positions by 50% and halts all new trade entries.
3. CRITICAL LEVEL (12-15% DD): Flattens all positions, executes full system halt, and alerts operator.

Recovery Protocol:
- After a CRITICAL event, requires 48 hours of clean paper trading before testnet resumption.
- Progressive Re-Entry: Starts at 25% normal sizing, increasing by 25% per clean trading week.
"""

import time
import datetime
from typing import Dict, List, Optional, Tuple, Any
from dataclasses import dataclass, field, asdict
from logger import get_logger

logger = get_logger("drawdown_controller")


@dataclass
class DrawdownStatus:
    peak_equity: float
    current_equity: float
    drawdown_pct: float
    level: str  # "NOMINAL", "WARNING_5PCT", "ACTION_8PCT", "CRITICAL_12PCT"
    position_size_multiplier: float = 1.0
    allow_new_entries: bool = True
    in_recovery_mode: bool = False
    clean_paper_hours_accumulated: float = 0.0
    progressive_reentry_tier: float = 1.0  # 0.25 -> 0.50 -> 0.75 -> 1.0

    # Backwards compatibility property
    @property
    def current_drawdown_pct(self) -> float:
        return self.drawdown_pct


class DrawdownController:
    """
    Supervises equity drawdowns and enforces multi-stage protective actions and progressive recovery.
    """

    def __init__(
        self,
        initial_equity: float = 5000.0,
        warning_drawdown_pct: float = 0.05,
        critical_drawdown_pct: float = 0.12,
        initial_capital: Optional[float] = None
    ):
        start_cap = initial_capital or initial_equity
        self.peak_equity = start_cap
        self.current_equity = start_cap
        self.warning_threshold_pct = warning_drawdown_pct * 100.0 if warning_drawdown_pct < 1.0 else warning_drawdown_pct
        self.critical_threshold_pct = critical_drawdown_pct * 100.0 if critical_drawdown_pct < 1.0 else critical_drawdown_pct
        self.status = DrawdownStatus(
            peak_equity=start_cap,
            current_equity=start_cap,
            drawdown_pct=0.0,
            level="NOMINAL"
        )
        self.last_critical_event_time: Optional[float] = None

    def update_equity(self, current_equity: float) -> DrawdownStatus:
        """Calculates current peak-to-trough drawdown and assigns risk level."""
        self.current_equity = current_equity
        if current_equity > self.peak_equity:
            self.peak_equity = current_equity

        dd_pct = ((self.peak_equity - current_equity) / max(self.peak_equity, 1.0)) * 100.0
        self.status.peak_equity = round(self.peak_equity, 2)
        self.status.current_equity = round(current_equity, 2)
        self.status.drawdown_pct = round(dd_pct, 2)

        # Evaluate Levels
        if dd_pct >= self.critical_threshold_pct:
            self.status.level = "CRITICAL_12PCT"
            self.status.position_size_multiplier = 0.0
            self.status.allow_new_entries = False
            self.status.in_recovery_mode = True
            if self.last_critical_event_time is None:
                self.last_critical_event_time = time.time()
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

        return self.status

    def get_defensive_action(self) -> Dict[str, Any]:
        """Returns defensive action dict for backward compatibility with test_advanced_risk.py."""
        if self.status.drawdown_pct >= self.critical_threshold_pct:
            return {"action": "HALT_AND_FLAT", "sizing_factor": 0.0}
        elif self.status.drawdown_pct >= self.warning_threshold_pct:
            return {"action": "THROTTLE_SIZING", "sizing_factor": 0.5}
        return {"action": "NONE", "sizing_factor": 1.0}

    def progress_recovery_paper_trading(self, hours_elapsed: float) -> Tuple[bool, float]:
        """Tracks 48h mandatory paper validation before testnet resumption."""
        if not self.status.in_recovery_mode:
            return True, 1.0

        self.status.clean_paper_hours_accumulated += hours_elapsed
        if self.status.clean_paper_hours_accumulated >= 48.0:
            self.status.in_recovery_mode = False
            self.status.progressive_reentry_tier = 0.25  # Start with 25% sizing
            self.status.position_size_multiplier = 0.25
            self.status.allow_new_entries = True
            logger.info("[DRAWDOWN_CTRL] 🟢 48h clean paper validation complete. Resuming with 25% sizing tier.")
            return True, 0.25

        return False, 0.0

    def advance_progressive_reentry(self) -> float:
        """Increases sizing by +25% after a clean trading week."""
        self.status.progressive_reentry_tier = min(1.0, self.status.progressive_reentry_tier + 0.25)
        self.status.position_size_multiplier = self.status.progressive_reentry_tier
        logger.info(f"[DRAWDOWN_CTRL] 📈 Advanced progressive re-entry tier to {int(self.status.progressive_reentry_tier*100)}%")
        return self.status.progressive_reentry_tier
