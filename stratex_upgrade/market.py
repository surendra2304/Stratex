from __future__ import annotations

import threading
import time
from collections.abc import Iterable
from dataclasses import dataclass
from decimal import Decimal


@dataclass(slots=True)
class FreshnessPolicy:
    max_age_seconds: float = 10.0
    require_sequence_monotonic: bool = True
    require_positive_prices: bool = True


class MarketDataGuard:
    """Reject stale, out-of-order or crossed snapshots before strategy/execution use."""

    def __init__(self, policy: FreshnessPolicy | None = None):
        self.policy = policy or FreshnessPolicy()
        self._last_seq: dict[str, int] = {}
        self._lock = threading.Lock()

    def accept(self, symbol: str, ts_ns: int, bid: Decimal | None, ask: Decimal | None, sequence: int | None) -> tuple[bool, str]:
        age = max(0.0, (time.time_ns() - ts_ns) / 1_000_000_000)
        if age > self.policy.max_age_seconds:
            return False, "STALE"
        if self.policy.require_positive_prices and (bid is not None and bid <= 0 or ask is not None and ask <= 0):
            return False, "INVALID_PRICE"
        if bid is not None and ask is not None and ask < bid:
            return False, "CROSSED_BOOK"
        if sequence is not None and self.policy.require_sequence_monotonic:
            with self._lock:
                previous = self._last_seq.get(symbol)
                if previous is not None and sequence <= previous:
                    return False, "OUT_OF_ORDER"
                self._last_seq[symbol] = sequence
        return True, "OK"


import itertools


def bar_gap_seconds(interval_seconds: int, timestamps_ns: Iterable[int]) -> list[float]:
    ts = list(timestamps_ns)
    out = []
    for a, b in itertools.pairwise(ts):
        delta = (b - a) / 1_000_000_000
        out.append(max(0.0, delta - interval_seconds))
    return out

