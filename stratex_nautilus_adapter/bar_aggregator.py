"""stratex_nautilus_adapter/bar_aggregator.py

NautilusTrader-inspired Multi-Type Bar Aggregation Engine:
- Tick Bars: aggregates fixed number of transactions (ticks)
- Volume Bars: aggregates fixed cumulative asset volume
- Value (Dollar) Bars: aggregates fixed cumulative traded notional ($)
- Time Bars: microsecond/second interval bars
Supports both streaming incremental tick processing and historical batch dataframe aggregation.
"""

from __future__ import annotations

from typing import Callable
import pandas as pd

from .models import Bar, BarType, TradeTick


class BarAggregator:
    """Base class for incremental bar aggregation from raw trade ticks."""

    def __init__(self, symbol: str, bar_type: BarType, step: float) -> None:
        self.symbol = symbol.upper()
        self.bar_type = bar_type
        self.step = float(step)
        self._current_ticks: list[TradeTick] = []
        self._on_bar_callback: Callable[[Bar], None] | None = None

    def set_callback(self, callback: Callable[[Bar], None]) -> None:
        self._on_bar_callback = callback

    def update(self, tick: TradeTick) -> Bar | None:
        """Appends tick and determines if bar completion threshold is reached."""
        raise NotImplementedError

    def _build_bar(self) -> Bar:
        ticks = self._current_ticks
        prices = [t.price for t in ticks]
        sizes = [t.size for t in ticks]
        notionals = [t.price * t.size for t in ticks]

        bar = Bar(
            bar_type=self.bar_type,
            symbol=self.symbol,
            step=self.step,
            open=prices[0],
            high=max(prices),
            low=min(prices),
            close=prices[-1],
            volume=sum(sizes),
            notional=sum(notionals),
            ticks_count=len(ticks),
            ts_start=ticks[0].ts_event,
            ts_end=ticks[-1].ts_event,
        )
        self._current_ticks = []
        if self._on_bar_callback:
            self._on_bar_callback(bar)
        return bar


class TickBarAggregator(BarAggregator):
    """Aggregates a bar every N trade ticks."""

    def __init__(self, symbol: str, step_ticks: int = 100) -> None:
        super().__init__(symbol, BarType.TICK, float(step_ticks))

    def update(self, tick: TradeTick) -> Bar | None:
        self._current_ticks.append(tick)
        if len(self._current_ticks) >= int(self.step):
            return self._build_bar()
        return None


class VolumeBarAggregator(BarAggregator):
    """Aggregates a bar whenever cumulative traded volume reaches threshold."""

    def __init__(self, symbol: str, step_volume: float = 10.0) -> None:
        super().__init__(symbol, BarType.VOLUME, float(step_volume))
        self._accumulated_volume: float = 0.0

    def update(self, tick: TradeTick) -> Bar | None:
        self._current_ticks.append(tick)
        self._accumulated_volume += tick.size
        if self._accumulated_volume >= self.step:
            self._accumulated_volume = 0.0
            return self._build_bar()
        return None


class ValueBarAggregator(BarAggregator):
    """Aggregates a bar whenever cumulative traded dollar/notional value reaches threshold."""

    def __init__(self, symbol: str, step_value_usd: float = 100000.0) -> None:
        super().__init__(symbol, BarType.VALUE, float(step_value_usd))
        self._accumulated_value: float = 0.0

    def update(self, tick: TradeTick) -> Bar | None:
        self._current_ticks.append(tick)
        self._accumulated_value += (tick.price * tick.size)
        if self._accumulated_value >= self.step:
            self._accumulated_value = 0.0
            return self._build_bar()
        return None


class TimeBarAggregator(BarAggregator):
    """Aggregates a bar whenever time interval elapsed exceeds step seconds."""

    def __init__(self, symbol: str, step_seconds: float = 60.0) -> None:
        super().__init__(symbol, BarType.TIME, float(step_seconds))
        self._start_ns: int | None = None
        self._step_ns = int(self.step * 1_000_000_000)

    def update(self, tick: TradeTick) -> Bar | None:
        if self._start_ns is None:
            self._start_ns = tick.ts_event

        self._current_ticks.append(tick)
        if (tick.ts_event - self._start_ns) >= self._step_ns:
            self._start_ns = None
            return self._build_bar()
        return None


class BarAggregatorEngine:
    """Unified engine to process tick streams or batch historical tick data into bars."""

    @staticmethod
    def aggregate_ticks(
        ticks: list[TradeTick],
        bar_type: BarType = BarType.TICK,
        step: float = 100.0,
    ) -> list[Bar]:
        """Iteratively processes a sequence of ticks into complete bars."""
        if not ticks:
            return []

        symbol = ticks[0].symbol
        if bar_type == BarType.TICK:
            agg = TickBarAggregator(symbol, int(step))
        elif bar_type == BarType.VOLUME:
            agg = VolumeBarAggregator(symbol, step)
        elif bar_type == BarType.VALUE:
            agg = ValueBarAggregator(symbol, step)
        elif bar_type == BarType.TIME:
            agg = TimeBarAggregator(symbol, step)
        else:
            raise ValueError(f"Unsupported bar type: {bar_type}")

        completed_bars: list[Bar] = []
        for tick in ticks:
            bar = agg.update(tick)
            if bar:
                completed_bars.append(bar)

        # Emit partial bar if requested and leftover ticks exist
        if agg._current_ticks:
            completed_bars.append(agg._build_bar())

        return completed_bars

    @staticmethod
    def bars_to_dataframe(bars: list[Bar]) -> pd.DataFrame:
        """Converts a list of Bar objects into a standardized pandas DataFrame."""
        if not bars:
            return pd.DataFrame(columns=[
                "ts_start", "ts_end", "open", "high", "low", "close",
                "volume", "notional", "ticks_count", "vwap"
            ])

        records = [
            {
                "ts_start": pd.to_datetime(b.ts_start, unit="ns", utc=True),
                "ts_end": pd.to_datetime(b.ts_end, unit="ns", utc=True),
                "open": b.open,
                "high": b.high,
                "low": b.low,
                "close": b.close,
                "volume": b.volume,
                "notional": b.notional,
                "ticks_count": b.ticks_count,
                "vwap": b.vwap,
            }
            for b in bars
        ]
        return pd.DataFrame(records)
