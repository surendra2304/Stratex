"""stratex_freqtrade_adapter/roi/roi_engine.py

Implements Freqtrade's time-decayed minimal_roi profit target engine.
"""

from __future__ import annotations
from typing import Dict, Tuple, Union


class ROIEngine:
    """Calculates dynamic minimal Return-On-Investment thresholds based on trade duration."""

    def __init__(self, minimal_roi: Dict[Union[str, int], float] | None = None):
        self._table: list[tuple[int, float]] = []
        if minimal_roi:
            for k, v in minimal_roi.items():
                try:
                    minute_key = int(k)
                    self._table.append((minute_key, float(v)))
                except (ValueError, TypeError):
                    continue
            # Sort ascending by minute duration
            self._table.sort(key=lambda x: x[0])

    @property
    def table(self) -> list[tuple[int, float]]:
        return list(self._table)

    def get_initial_target(self) -> float:
        """Returns the initial target (duration = 0), or the earliest configured tier."""
        if not self._table:
            return 0.05  # Default 5%
        return self._table[0][1]

    def get_target(self, duration_minutes: float) -> float:
        """Finds the applicable profit threshold for a given duration in minutes."""
        if not self._table:
            return float("inf")

        # Find the highest minute threshold <= duration_minutes
        active_target = self._table[0][1]
        for minutes, target in self._table:
            if duration_minutes >= minutes:
                active_target = target
            else:
                break
        return active_target

    def should_exit(self, duration_minutes: float, current_profit: float) -> Tuple[bool, float]:
        """Evaluates whether current trade profit satisfies minimal_roi.
        Returns (should_exit, target_profit).
        """
        if not self._table:
            return False, 0.0

        target = self.get_target(duration_minutes)
        if current_profit >= target:
            return True, target
        return False, target
