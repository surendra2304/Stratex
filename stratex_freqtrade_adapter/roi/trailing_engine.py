"""stratex_freqtrade_adapter/roi/trailing_engine.py

Implements Freqtrade's exact trailing stop loss state machine.
Supports positive trailing, offset activation gates, and peak ratchets.
"""

from __future__ import annotations
from typing import Optional, Tuple

from numeric_safety import finite_float, positive_float


class TrailingStopEngine:
    """State machine and calculator for Freqtrade-style trailing stop loss."""

    def __init__(
        self,
        trailing_stop: bool = False,
        trailing_stop_positive: float = 0.01,
        trailing_stop_positive_offset: float = 0.02,
        trailing_only_offset_is_reached: bool = False,
    ):
        positive = finite_float(trailing_stop_positive)
        offset = finite_float(trailing_stop_positive_offset)
        if positive is None or not 0.0 < positive < 1.0:
            raise ValueError(f"trailing_stop_positive must be within (0, 1), got {trailing_stop_positive!r}")
        if offset is None or offset < 0.0:
            raise ValueError(f"trailing_stop_positive_offset must be >= 0, got {trailing_stop_positive_offset!r}")
        self.trailing_stop = bool(trailing_stop)
        self.trailing_stop_positive = positive
        self.trailing_stop_positive_offset = offset
        self.trailing_only_offset_is_reached = bool(trailing_only_offset_is_reached)

    def calculate_stop_price(
        self,
        side: str,
        open_rate: float,
        current_rate: float,
        max_rate: float,
    ) -> Optional[float]:
        """Calculates trailing stop price if active; returns None if not armed.

        Returns None for unreadable rates or an unknown side (a NaN peak used to
        produce a NaN stop that ``current <= stop`` could never trigger, and any
        side other than BUY/LONG was silently treated as a short).
        """
        open_rate = positive_float(open_rate)
        current_rate = positive_float(current_rate)
        max_rate = positive_float(max_rate)
        side_u = str(side).upper()
        if not self.trailing_stop or open_rate is None or current_rate is None or max_rate is None:
            return None
        if side_u not in ("BUY", "LONG", "SELL", "SHORT"):
            return None
        is_long = side_u in ("BUY", "LONG")
        if is_long:
            peak_profit = (max_rate - open_rate) / open_rate

            if self.trailing_only_offset_is_reached and peak_profit < self.trailing_stop_positive_offset:
                return None

            # Stop is trailing_stop_positive below the peak rate
            stop_price = max_rate * (1.0 - self.trailing_stop_positive)
            return stop_price
        else:
            # Short position: max_rate corresponds to lowest rate seen (trough)
            min_rate = max_rate  # caller passes best price seen
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
        current_rate = float(current_rate)
        is_long = str(side).upper() in ("BUY", "LONG")
        if is_long and current_rate <= stop_price:
            return True, stop_price
        elif not is_long and current_rate >= stop_price:
            return True, stop_price

        return False, stop_price
