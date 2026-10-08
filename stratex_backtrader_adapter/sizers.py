"""stratex_backtrader_adapter/sizers.py

Decoupled Position Sizer hierarchy inspired by Backtrader:
- BaseSizer: abstract contract for sizing calculations
- FixedSize: static unit sizing
- PercentSizer: dynamic capital allocation as % of portfolio value
- VolatilitySizer: ATR-based risk budgeting (fixed $ risk / stop distance)
- KellySizer: Kelly criterion sizing based on win rate and payoff ratio

Every sizer validates its parameters at construction (a ``FixedSize(-1)`` or
``FixedSize(nan)`` used to emit negative/NaN order sizes) and every computed
size is finite and >= 0. ``VolatilitySizer`` no longer invents a "2% of price"
ATR when none is supplied: without a measured ATR it does not size a trade.
"""

from __future__ import annotations

import math
from typing import TYPE_CHECKING

from numeric_safety import finite_float, positive_float

if TYPE_CHECKING:
    from .broker import BacktraderBroker


def _param(name: str, value, *, low: float = 0.0, high: float = math.inf, allow_zero: bool = False) -> float:
    number = finite_float(value)
    if number is None or number < low or number > high or (number == 0 and not allow_zero):
        raise ValueError(f"sizer parameter {name}={value!r} must be a finite number in "
                         f"{'[' if allow_zero else '('}{low}, {high}]")
    return number


def _size(value: float) -> float:
    return value if math.isfinite(value) and value > 0 else 0.0


def _portfolio_value(broker: BacktraderBroker, symbol: str, price: float) -> float | None:
    value = positive_float(broker.get_value({symbol: price}))
    return value


class BaseSizer:
    """Abstract base class for Backtrader position sizing."""

    def __init__(self, **params) -> None:
        self.params = params

    def get_size(
        self,
        broker: BacktraderBroker,
        symbol: str,
        price: float,
        **kwargs,
    ) -> float:
        """Computes order quantity to execute."""
        raise NotImplementedError


class FixedSize(BaseSizer):
    """Allocates a fixed number of units / contracts per trade."""

    def __init__(self, size: float = 1.0, **params) -> None:
        super().__init__(**params)
        self.default_size = _param("size", size)

    def get_size(
        self,
        broker: BacktraderBroker,
        symbol: str,
        price: float,
        **kwargs,
    ) -> float:
        if positive_float(price) is None:
            return 0.0
        return self.default_size


class PercentSizer(BaseSizer):
    """Allocates a fixed percentage of current portfolio equity."""

    def __init__(self, percent: float = 10.0, **params) -> None:
        super().__init__(**params)
        self.percent = _param("percent", percent, high=100.0)

    def get_size(
        self,
        broker: BacktraderBroker,
        symbol: str,
        price: float,
        **kwargs,
    ) -> float:
        p = positive_float(price)
        if p is None:
            return 0.0
        portfolio_val = _portfolio_value(broker, symbol, p)
        if portfolio_val is None:
            return 0.0
        return _size(portfolio_val * (self.percent / 100.0) / p)


class VolatilitySizer(BaseSizer):
    """Allocates position size inversely proportional to market volatility (ATR).

    Formula: Size = (Equity * RiskPct) / (ATR * ATR_Multiplier)
    """

    def __init__(
        self,
        risk_pct: float = 1.0,
        atr_multiplier: float = 2.0,
        **params,
    ) -> None:
        super().__init__(**params)
        self.risk_pct = _param("risk_pct", risk_pct, high=100.0)
        self.atr_multiplier = _param("atr_multiplier", atr_multiplier)

    def get_size(
        self,
        broker: BacktraderBroker,
        symbol: str,
        price: float,
        atr: float | None = None,
        **kwargs,
    ) -> float:
        p = positive_float(price)
        val_atr = positive_float(atr if atr is not None else kwargs.get("atr"))
        if p is None or val_atr is None:
            return 0.0
        stop_distance = val_atr * self.atr_multiplier
        portfolio_val = _portfolio_value(broker, symbol, p)
        if portfolio_val is None:
            return 0.0
        return _size(portfolio_val * (self.risk_pct / 100.0) / stop_distance)


class KellySizer(BaseSizer):
    """Allocates position size using the Kelly Criterion.

    K = WinRate - ((1 - WinRate) / PayoffRatio)
    Fractional Kelly applies a dampening factor (e.g. 0.5 for Half-Kelly).
    """

    def __init__(
        self,
        win_rate: float = 0.55,
        payoff_ratio: float = 1.5,
        fraction: float = 0.5,
        max_pct: float = 25.0,
        **params,
    ) -> None:
        super().__init__(**params)
        self.win_rate = _param("win_rate", win_rate, high=1.0, allow_zero=True)
        self.payoff_ratio = _param("payoff_ratio", payoff_ratio)
        self.fraction = _param("fraction", fraction, high=1.0)
        self.max_pct = _param("max_pct", max_pct, high=100.0)

    def get_size(
        self,
        broker: BacktraderBroker,
        symbol: str,
        price: float,
        **kwargs,
    ) -> float:
        p = positive_float(price)
        if p is None:
            return 0.0
        k = self.win_rate - ((1.0 - self.win_rate) / self.payoff_ratio)
        kelly_fraction = max(0.0, k * self.fraction)
        # Cap at max_pct
        capped_pct = min(kelly_fraction * 100.0, self.max_pct)
        portfolio_val = _portfolio_value(broker, symbol, p)
        if portfolio_val is None:
            return 0.0
        return _size(portfolio_val * (capped_pct / 100.0) / p)
