"""Offline Jarvis-style short/medium moving-average signals; not investment advice."""
from __future__ import annotations

from dataclasses import dataclass
import math


@dataclass(frozen=True)
class MomentumReport:
    symbol: str
    price: float
    change: float
    percent_change: float
    short_average: float | None
    long_average: float | None
    signal: str


class MarketMomentumEngine:
    """Analyze supplied prices, without inventing live market data."""

    def analyze(self, symbol: str, price: float, previous_close: float,
                closes: list[float | None]) -> MomentumReport:
        if not symbol.strip() or any(not math.isfinite(x) or x <= 0 for x in (price, previous_close)):
            raise ValueError("symbol and finite positive prices required")
        values = [x for x in closes if x is not None]
        if any(not math.isfinite(x) or x <= 0 for x in values):
            raise ValueError("closes must be positive finite prices or None")
        change = price - previous_close
        percent = change / previous_close * 100
        short = long = None
        signal = "HOLD"
        if len(values) >= 10:
            short = sum(values[-5:]) / 5
            long = sum(values[-15:]) / min(15, len(values))
            if price > short > long and percent > 0.5:
                signal = "STRONG BUY"
            elif price > short and percent >= 0:
                signal = "BUY / ACCUMULATE"
            elif price < short < long and percent < -0.5:
                signal = "STRONG SELL / SHORT"
            elif price < short:
                signal = "SELL / TAKE PROFIT"
        return MomentumReport(symbol, price, change, percent, short, long, signal)
