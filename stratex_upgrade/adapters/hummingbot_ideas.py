"""Hummingbot-inspired explicit order tracking and lifecycle helpers."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class TrackerState(str, Enum):
    UNTRACKED = "UNTRACKED"
    TRACKING = "TRACKING"
    DONE = "DONE"
    LOST = "LOST"


@dataclass(slots=True)
class TrackedOrder:
    order_id: str
    client_order_id: str
    symbol: str
    state: TrackerState = TrackerState.TRACKING
    filled: float = 0.0

    def mark_fill(self, quantity: float) -> None:
        self.filled += quantity
        if self.filled > 0:
            self.state = TrackerState.TRACKING

    def mark_done(self) -> None:
        self.state = TrackerState.DONE

    def mark_lost(self) -> None:
        self.state = TrackerState.LOST
