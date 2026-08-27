"""
risk/drawdown_controller.py — Real-Time Drawdown Tracking & Circuit Breaker Controller.

Tracks:
1. High-Water Mark & Peak-to-Trough Drawdown in real time.
2. Drawdown duration (bars/hours in underwater state).
3. Multi-tier defensive actions:
   - Sizing attenuation
   - Strategy probation / temporary pause
   - Hard circuit breaker trip & complete liquidation / flat state.
"""

import time
import math
from typing import Dict, List, Optional, Tuple, Any
from dataclasses import dataclass, field


@dataclass
class DrawdownState:
    high_water_mark: float = 10000.0
    current_equity: float = 10000.0
    current_drawdown_pct: float = 0.0
    max_drawdown_pct: float = 0.0
    is_underwater: bool = False
    underwater_start_time: Optional[float] = None
    underwater_duration_hours: float = 0.0
    circuit_breaker_active: bool = False


class DrawdownController:
    """
    Supervises portfolio equity against historical peak, calculating underwater metrics
    and commanding defensive risk throttling.
    """

    def __init__(
        self,
        warning_drawdown_pct: float = 0.08,
        critical_drawdown_pct: float = 0.15,
        initial_capital: float = 10000.0
    ):
        self.warning_threshold = warning_drawdown_pct * 100.0 if warning_drawdown_pct <= 1.0 else warning_drawdown_pct
        self.critical_threshold = critical_drawdown_pct * 100.0 if critical_drawdown_pct <= 1.0 else critical_drawdown_pct
        self.state = DrawdownState(
            high_water_mark=initial_capital,
            current_equity=initial_capital
        )

    def update_equity(self, current_equity: float) -> DrawdownState:
        """
        Updates equity, recomputes high-water mark and drawdown percentage.
        """
        now = time.time()
        self.state.current_equity = current_equity

        if current_equity > self.state.high_water_mark:
            self.state.high_water_mark = current_equity
            self.state.is_underwater = False
            self.state.underwater_start_time = None
            self.state.underwater_duration_hours = 0.0
            self.state.current_drawdown_pct = 0.0
        else:
            hwm = self.state.high_water_mark
            dd = ((hwm - current_equity) / hwm) * 100.0 if hwm > 0 else 0.0
            self.state.current_drawdown_pct = round(dd, 2)
            self.state.max_drawdown_pct = max(self.state.max_drawdown_pct, self.state.current_drawdown_pct)

            if not self.state.is_underwater:
                self.state.is_underwater = True
                self.state.underwater_start_time = now

            if self.state.underwater_start_time:
                self.state.underwater_duration_hours = round((now - self.state.underwater_start_time) / 3600.0, 2)

            if self.state.current_drawdown_pct >= self.critical_threshold:
                self.state.circuit_breaker_active = True

        return self.state

    def get_defensive_action(self) -> Dict[str, Any]:
        """
        Returns recommended defensive positioning based on current drawdown state.
        """
        dd = self.state.current_drawdown_pct
        if dd >= self.critical_threshold:
            return {
                "action": "HALT_AND_FLAT",
                "sizing_factor": 0.0,
                "reason": f"Critical drawdown ceiling exceeded ({dd:.2f}% >= {self.critical_threshold:.1f}%)"
            }
        elif dd >= self.warning_threshold:
            # Linear reduction from 1.0 down to 0.4 between warning and critical
            span = self.critical_threshold - self.warning_threshold
            progress = (dd - self.warning_threshold) / max(span, 1e-4)
            factor = 1.0 - (progress * 0.6)
            return {
                "action": "THROTTLE_SIZING",
                "sizing_factor": round(factor, 2),
                "reason": f"Drawdown in warning corridor ({dd:.2f}%)"
            }
        else:
            return {
                "action": "NORMAL",
                "sizing_factor": 1.0,
                "reason": "Drawdown within standard tolerance"
            }
