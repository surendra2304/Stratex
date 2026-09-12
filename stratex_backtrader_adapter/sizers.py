"""stratex_backtrader_adapter/sizers.py

Decoupled Position Sizer hierarchy inspired by Backtrader:
- BaseSizer: abstract contract for sizing calculations
- FixedSize: static unit sizing
- PercentSizer: dynamic capital allocation as % of portfolio value
- VolatilitySizer: ATR-based risk budgeting (fixed $ risk / stop distance)
- KellySizer: Kelly criterion sizing based on win rate and payoff ratio
"""

from __future__ import annotations

from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from .broker import BacktraderBroker


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
        self.default_size = float(size)

    def get_size(
        self,
        broker: BacktraderBroker,
        symbol: str,
        price: float,
        **kwargs,
    ) -> float:
        return self.default_size


class PercentSizer(BaseSizer):
    """Allocates a fixed percentage of current portfolio equity."""

    def __init__(self, percent: float = 10.0, **params) -> None:
        super().__init__(**params)
        self.percent = float(percent)

    def get_size(
        self,
        broker: BacktraderBroker,
        symbol: str,
        price: float,
        **kwargs,
    ) -> float:
        if price <= 0:
            return 0.0
        portfolio_val = broker.get_value({symbol: price})
        target_allocation = portfolio_val * (self.percent / 100.0)
        return max(0.0, target_allocation / price)


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
        self.risk_pct = float(risk_pct)
        self.atr_multiplier = float(atr_multiplier)

    def get_size(
        self,
        broker: BacktraderBroker,
        symbol: str,
        price: float,
        atr: float | None = None,
        **kwargs,
    ) -> float:
        val_atr = atr or kwargs.get("atr") or (price * 0.02)  # 2% price proxy fallback
        stop_distance = val_atr * self.atr_multiplier
        if stop_distance <= 0:
            return 0.0

        portfolio_val = broker.get_value({symbol: price})
        risk_amount = portfolio_val * (self.risk_pct / 100.0)
        return max(0.0, risk_amount / stop_distance)


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
        self.win_rate = float(win_rate)
        self.payoff_ratio = float(payoff_ratio)
        self.fraction = float(fraction)
        self.max_pct = float(max_pct)

    def get_size(
        self,
        broker: BacktraderBroker,
        symbol: str,
        price: float,
        **kwargs,
    ) -> float:
        if price <= 0 or self.payoff_ratio <= 0:
            return 0.0

        k = self.win_rate - ((1.0 - self.win_rate) / self.payoff_ratio)
        kelly_fraction = max(0.0, k * self.fraction)
        # Cap at max_pct
        capped_pct = min(kelly_fraction * 100.0, self.max_pct)

        portfolio_val = broker.get_value({symbol: price})
        target_allocation = portfolio_val * (capped_pct / 100.0)
        return max(0.0, target_allocation / price)
