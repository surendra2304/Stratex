"""stratex_nautilus_adapter/bar_aggregator.py

NautilusTrader-inspired Multi-Type Bar Aggregation Engine:
- Tick Bars: aggregates fixed number of transactions (ticks)
- Volume Bars: aggregates fixed cumulative asset volume
- Value (Dollar) Bars: aggregates fixed cumulative traded notional ($)
- Time Bars: microsecond/second interval bars
Supports both streaming incremental tick processing and historical batch dataframe aggregation.
"""

from __future__ import annotations

import math
from numbers import Integral, Real
from typing import Callable

import pandas as pd

from .models import Bar, BarType, TradeTick


class BarAggregator:
    """Base class for incremental bar aggregation from raw trade ticks."""

    def __init__(self, symbol: str, bar_type: BarType, step: float) -> None:
        if not isinstance(symbol, str) or not symbol.strip():
            raise ValueError("symbol must be a non-empty string")
        if not isinstance(bar_type, BarType):
            raise ValueError("bar_type must be a BarType value")
        if (
            isinstance(step, bool)
            or not isinstance(step, Real)
            or not math.isfinite(float(step))
            or float(step) <= 0.0
        ):
            raise ValueError("step must be a finite positive number")

        self.symbol = symbol.strip().upper()
        self.bar_type = bar_type
        self.step = float(step)
        self._current_ticks: list[TradeTick] = []
        self._current_volume = 0.0
        self._current_notional = 0.0
        self._on_bar_callback: Callable[[Bar], None] | None = None
        self._last_ts_event: int | None = None

    def set_callback(self, callback: Callable[[Bar], None]) -> None:
        if not callable(callback):
            raise TypeError("callback must be callable")
        self._on_bar_callback = callback

    def _validate_tick(self, tick: TradeTick) -> None:
        """Reject malformed or out-of-order market data before mutating bar state."""
        if not isinstance(tick, TradeTick):
            raise TypeError("tick must be a TradeTick")
        if not isinstance(tick.symbol, str) or tick.symbol.strip().upper() != self.symbol:
            raise ValueError(f"tick symbol must match aggregator symbol {self.symbol}")

        for field_name in ("price", "size"):
            value = getattr(tick, field_name)
            if (
                isinstance(value, bool)
                or not isinstance(value, Real)
                or not math.isfinite(float(value))
                or float(value) <= 0.0
            ):
                raise ValueError(f"tick {field_name} must be a finite positive number")

        tick_notional = float(tick.price) * float(tick.size)
        if not math.isfinite(tick_notional) or tick_notional <= 0.0:
            raise ValueError("tick notional must be a finite positive number")
        if not math.isfinite(self._current_volume + float(tick.size)):
            raise ValueError("aggregated bar volume must remain finite")
        if not math.isfinite(self._current_notional + tick_notional):
            raise ValueError("aggregated bar notional must remain finite")

        timestamp = tick.ts_event
        if isinstance(timestamp, bool) or not isinstance(timestamp, Integral) or timestamp < 0:
            raise ValueError("tick ts_event must be a non-negative integer nanosecond timestamp")
        timestamp = int(timestamp)
        if self._last_ts_event is not None and timestamp < self._last_ts_event:
            raise ValueError("tick timestamps must be non-decreasing")

        # Commit validation state only after every field passes.
        self._last_ts_event = timestamp

    def _append_validated_tick(self, tick: TradeTick) -> None:
        """Append validated data and update finite cumulative bar measurements."""
        self._current_ticks.append(tick)
        self._current_volume += float(tick.size)
        self._current_notional += float(tick.price) * float(tick.size)

    def _append_tick(self, tick: TradeTick) -> None:
        """Validate a tick, then atomically add it to the in-progress bar."""
        self._validate_tick(tick)
        self._append_validated_tick(tick)

    def update(self, tick: TradeTick) -> Bar | None:
        """Appends tick and determines if bar completion threshold is reached."""
        raise NotImplementedError

    def _build_bar(self) -> Bar:
        ticks = self._current_ticks
        prices = [t.price for t in ticks]
        bar = Bar(
            bar_type=self.bar_type,
            symbol=self.symbol,
            step=self.step,
            open=prices[0],
            high=max(prices),
            low=min(prices),
            close=prices[-1],
            volume=self._current_volume,
            notional=self._current_notional,
            ticks_count=len(ticks),
            ts_start=ticks[0].ts_event,
            ts_end=ticks[-1].ts_event,
        )
        self._current_ticks = []
        self._current_volume = 0.0
        self._current_notional = 0.0
        if self._on_bar_callback:
            self._on_bar_callback(bar)
        return bar


class TickBarAggregator(BarAggregator):
    """Aggregates a bar every N trade ticks."""

    def __init__(self, symbol: str, step_ticks: int = 100) -> None:
        if (
            isinstance(step_ticks, bool)
            or not isinstance(step_ticks, Real)
            or not math.isfinite(float(step_ticks))
            or int(step_ticks) != step_ticks
            or step_ticks < 1
        ):
            raise ValueError("step_ticks must be a positive integer")
        super().__init__(symbol, BarType.TICK, float(int(step_ticks)))

    def update(self, tick: TradeTick) -> Bar | None:
        self._append_tick(tick)
        if len(self._current_ticks) >= int(self.step):
            return self._build_bar()
        return None


class VolumeBarAggregator(BarAggregator):
    """Aggregates a bar whenever cumulative traded volume reaches threshold."""

    def __init__(self, symbol: str, step_volume: float = 10.0) -> None:
        super().__init__(symbol, BarType.VOLUME, step_volume)
        self._accumulated_volume: float = 0.0

    def update(self, tick: TradeTick) -> Bar | None:
        self._append_tick(tick)
        self._accumulated_volume += tick.size
        if self._accumulated_volume >= self.step:
            self._accumulated_volume = 0.0
            return self._build_bar()
        return None


class ValueBarAggregator(BarAggregator):
    """Aggregates a bar whenever cumulative traded dollar/notional value reaches threshold."""

    def __init__(self, symbol: str, step_value_usd: float = 100000.0) -> None:
        super().__init__(symbol, BarType.VALUE, step_value_usd)
        self._accumulated_value: float = 0.0

    def update(self, tick: TradeTick) -> Bar | None:
        self._append_tick(tick)
        self._accumulated_value += (tick.price * tick.size)
        if self._accumulated_value >= self.step:
            self._accumulated_value = 0.0
            return self._build_bar()
        return None


class TimeBarAggregator(BarAggregator):
    """Aggregates a bar whenever time interval elapsed exceeds step seconds."""

    def __init__(self, symbol: str, step_seconds: float = 60.0) -> None:
        super().__init__(symbol, BarType.TIME, step_seconds)
        self._start_ns: int | None = None
        self._step_ns = int(self.step * 1_000_000_000)
        if self._step_ns < 1:
            raise ValueError("step_seconds must be at least one nanosecond")

    def update(self, tick: TradeTick) -> Bar | None:
        self._validate_tick(tick)
        if self._start_ns is None:
            self._start_ns = tick.ts_event

        self._append_validated_tick(tick)
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
            agg = TickBarAggregator(symbol, step)
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
