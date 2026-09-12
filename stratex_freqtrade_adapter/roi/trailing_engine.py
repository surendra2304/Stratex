"""stratex_freqtrade_adapter/roi/trailing_engine.py

Implements Freqtrade's exact trailing stop loss state machine.
Supports positive trailing, offset activation gates, and peak ratchets.
"""

from __future__ import annotations
from typing import Optional, Tuple


class TrailingStopEngine:
    """State machine and calculator for Freqtrade-style trailing stop loss."""

    def __init__(
        self,
        trailing_stop: bool = False,
        trailing_stop_positive: float = 0.01,
        trailing_stop_positive_offset: float = 0.02,
        trailing_only_offset_is_reached: bool = False,
    ):
        self.trailing_stop = trailing_stop
        self.trailing_stop_positive = float(trailing_stop_positive)
        self.trailing_stop_positive_offset = float(trailing_stop_positive_offset)
        self.trailing_only_offset_is_reached = trailing_only_offset_is_reached

    def calculate_stop_price(
        self,
        side: str,
        open_rate: float,
        current_rate: float,
        max_rate: float,
    ) -> Optional[float]:
        """Calculates trailing stop price if active; returns None if not armed."""
        if not self.trailing_stop or open_rate <= 0:
            return None

        is_long = side.upper() in ("BUY", "LONG")

        if is_long:
            current_profit = (current_rate - open_rate) / open_rate
            peak_profit = (max_rate - open_rate) / open_rate

            if self.trailing_only_offset_is_reached and peak_profit < self.trailing_stop_positive_offset:
                return None

            # Stop is trailing_stop_positive below the peak rate
            stop_price = max_rate * (1.0 - self.trailing_stop_positive)
            return stop_price
        else:
            # Short position: max_rate corresponds to lowest rate seen (trough)
            min_rate = max_rate  # caller passes best price seen
            current_profit = (open_rate - current_rate) / open_rate
            peak_profit = (open_rate - min_rate) / open_rate

            if self.trailing_only_offset_is_reached and peak_profit < self.trailing_stop_positive_offset:
                return None

            # Stop is trailing_stop_positive above the trough rate
            stop_price = min_rate * (1.0 + self.trailing_stop_positive)
            return stop_price

    def should_exit(
        self,
        side: str,
        open_rate: float,
        current_rate: float,
        max_rate: float,
    ) -> Tuple[bool, Optional[float]]:
        """Determines if current_rate violates the trailing stop.
        Returns (should_exit, stop_price).
        """
        stop_price = self.calculate_stop_price(side, open_rate, current_rate, max_rate)
        if stop_price is None:
            return False, None

        is_long = side.upper() in ("BUY", "LONG")
        if is_long and current_rate <= stop_price:
            return True, stop_price
        elif not is_long and current_rate >= stop_price:
            return True, stop_price

        return False, stop_price
