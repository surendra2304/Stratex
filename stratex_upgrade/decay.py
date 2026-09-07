"""
stratex_upgrade/decay.py — Qanat-inspired Signal & Weight Decay Smoother.

Mathematical formulation based on Qanat (fidetolabs/qanat):
    "The raw alpha is right about direction and wrong about how often,
    so acting on every twitch pays fees for noise. Weights are averaged
    over the last n stops, newest heaviest (linear, n, n-1, ... 1) —
    the signal survives, the turnover falls."

Formula:
    For a history window of M <= N signals [s_1, s_2, ..., s_M]:
    weights = [1, 2, ..., M]
    blended_signal = sum(weights[i] * s_{i+1} for i in range(M)) / sum(weights)

A signal is qualified only when blended conviction passes the threshold (default >= 0.50).
This slashes churn, eliminates whipsaw commission drag, and stabilizes systematic alpha.
"""

from __future__ import annotations

import collections
from dataclasses import dataclass


@dataclass
class DecayResult:
    raw_signal: float            # +1.0 (BUY), -1.0 (SELL), 0.0 (NEUTRAL)
    smoothed_signal: float       # Blended continuous value in [-1.0, 1.0]
    conviction: float            # Absolute smoothed strength in [0.0, 1.0]
    persisted_bars: int          # How many consecutive bars signal has persisted
    is_confirmed: bool           # True if smoothed conviction >= threshold
    action: str                  # "BUY", "SELL", or "HOLD"/"FILTERED"


class SignalDecaySmoother:
    """
    Tracks and blends signals across consecutive candle closes per (symbol, timeframe, strategy).
    """

    def __init__(self, decay_steps: int = 4, confirmation_threshold: float = 0.50):
        """
        Parameters
        ----------
        decay_steps : int
            Number of recent bars over which to blend signals (default 4, matching Qanat standard).
        confirmation_threshold : float
            Minimum smoothed conviction required to confirm an entry (default 0.50).
        """
        self.decay_steps = max(1, int(decay_steps))
        self.confirmation_threshold = float(confirmation_threshold)
        # Key: (symbol, timeframe, strategy) -> deque of raw signals [-1.0, 0.0, 1.0]
        self._history: dict[tuple[str, str, str], collections.deque[float]] = collections.defaultdict(
            lambda: collections.deque(maxlen=self.decay_steps)
        )
        self._consecutive_persistence: dict[tuple[str, str, str], int] = collections.defaultdict(int)
        
        # Diagnostics
        self.total_signals_received = 0
        self.total_signals_confirmed = 0
        self.total_signals_filtered_as_noise = 0

    def update(
        self,
        symbol: str,
        timeframe: str,
        strategy: str,
        side: str | None,
        confidence: float = 1.0,
    ) -> DecayResult:
        """
        Ingest a new raw bar signal and compute its linear decayed value.

        Parameters
        ----------
        symbol : str
            Trading pair symbol, e.g. "BTCUSDT".
        timeframe : str
            Bar timeframe, e.g. "15m", "1h".
        strategy : str
            Strategy identifier.
        side : str | None
            "BUY" / "LONG" -> +1.0 * confidence
            "SELL" / "SHORT" -> -1.0 * confidence
            None / "HOLD" -> 0.0
        confidence : float
            Raw confidence multiplier in [0.0, 1.0].

        Returns
        -------
        DecayResult
        """
        self.total_signals_received += 1
        key = (symbol, timeframe, strategy)

        # Convert side to numeric
        if side in ("BUY", "LONG"):
            raw_val = 1.0 * max(0.0, min(1.0, confidence))
        elif side in ("SELL", "SHORT"):
            raw_val = -1.0 * max(0.0, min(1.0, confidence))
        else:
            raw_val = 0.0

        hist = self._history[key]
        hist.append(raw_val)

        # Update persistence count
        if raw_val > 0:
            if self._consecutive_persistence[key] >= 0:
                self._consecutive_persistence[key] += 1
            else:
                self._consecutive_persistence[key] = 1
        elif raw_val < 0:
            if self._consecutive_persistence[key] <= 0:
                self._consecutive_persistence[key] -= 1
            else:
                self._consecutive_persistence[key] = -1
        else:
            self._consecutive_persistence[key] = 0

        persisted_bars = abs(self._consecutive_persistence[key])

        # If decay is turned off (decay_steps <= 1), use raw signal directly
        if self.decay_steps <= 1 or len(hist) <= 1:
            smoothed = raw_val
        else:
            # Linear weights: oldest 1 ... newest M
            m = len(hist)
            weights = list(range(1, m + 1))
            total_w = float(sum(weights))
            smoothed = sum(w * val for w, val in zip(weights, hist)) / total_w

        conviction = abs(smoothed)
        is_confirmed = conviction >= self.confirmation_threshold

        if is_confirmed and smoothed > 0:
            action = "BUY"
            self.total_signals_confirmed += 1
        elif is_confirmed and smoothed < 0:
            action = "SELL"
            self.total_signals_confirmed += 1
        else:
            action = "FILTERED" if raw_val != 0.0 else "HOLD"
            if raw_val != 0.0:
                self.total_signals_filtered_as_noise += 1

        return DecayResult(
            raw_signal=raw_val,
            smoothed_signal=smoothed,
            conviction=conviction,
            persisted_bars=persisted_bars,
            is_confirmed=is_confirmed,
            action=action,
        )

    def reset(self, symbol: str | None = None):
        """Reset signal history for a symbol or completely."""
        if symbol is None:
            self._history.clear()
            self._consecutive_persistence.clear()
        else:
            keys_to_del = [k for k in self._history if k[0] == symbol]
            for k in keys_to_del:
                del self._history[k]
                del self._consecutive_persistence[k]
