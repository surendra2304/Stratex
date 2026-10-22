"""
risk/dynamic_risk_manager.py — Dynamic Quantitative Risk Management & Risk Budgeting Engine.

Features:
1. Sizing Models: Fixed Fractional, ATR/Volatility-based, Kelly Criterion (half/full), Risk Parity.
2. Dynamic Risk Scaling: Modulated by portfolio drawdown, market volatility regimes, and position correlations.
3. Strict Risk Budgeting: Max risk per trade, portfolio VaR/CVaR limits, daily/weekly/monthly loss caps, concentration limits.
4. Risk Metrics & Stress Testing: Historical/Parametric VaR (95%/99%), CVaR (Expected Shortfall), and simulated market shock scenarios.
"""

import math
from dataclasses import dataclass

import numpy as np

from numeric_safety import finite_float, finite_values, positive_float

# Every sizing model returns 0.0 (no trade) for invalid input. Previously NaN
# inputs passed the ``<= 0`` guards (NaN compares False) and propagated:
# fixed-fractional/volatility sizes came back NaN, an infinite equity produced
# an infinite size, a NaN win rate was clamped by ``min(0.99, nan)`` to 0.99 —
# the *largest* Kelly bet — and a NaN drawdown kept 100% sizing.


@dataclass
class RiskBudget:
    max_risk_per_trade_pct: float = 0.01        # 1.0% equity per trade
    max_portfolio_risk_pct: float = 0.05        # 5.0% aggregate active portfolio risk
    max_daily_loss_pct: float = 0.03            # 3.0% daily loss limit
    max_weekly_loss_pct: float = 0.07           # 7.0% weekly loss limit
    max_monthly_loss_pct: float = 0.12          # 12.0% monthly loss limit
    max_asset_concentration_pct: float = 0.25   # Max 25% notional in single asset
    max_sector_concentration_pct: float = 0.50  # Max 50% notional in single sector
    max_leverage: float = 1.0

    def __post_init__(self) -> None:
        for name, value in vars(self).items():
            number = finite_float(value)
            if number is None or number < 0:
                raise ValueError(f"RiskBudget.{name}={value!r} must be a finite number >= 0")
            if name.endswith("_pct") and number > 1.0:
                raise ValueError(f"RiskBudget.{name}={value!r} is a fraction and must be <= 1.0")


class DynamicRiskManager:
    """
    Evaluates real-time risk, computes position sizes across multiple quantitative models,
    and enforces hard budget limits.
    """

    def __init__(self, budget: RiskBudget | None = None):
        self.budget = budget or RiskBudget()

    def calculate_fixed_fractional_size(
        self,
        equity: float,
        entry_price: float,
        stop_loss_price: float,
        fraction_pct: float | None = None
    ) -> float:
        """
        Fixed fractional sizing: Risk = Equity * fraction_pct.
        Position Qty = Risk / abs(entry_price - stop_loss_price).
        """
        equity_v = positive_float(equity)
        entry = positive_float(entry_price)
        stop = positive_float(stop_loss_price)
        if equity_v is None or entry is None or stop is None:
            return 0.0
        if fraction_pct is None:
            risk_pct = self.budget.max_risk_per_trade_pct
        else:
            requested = finite_float(fraction_pct)
            if requested is None or requested <= 0:
                return 0.0  # an explicit 0%/invalid risk request means no trade
            risk_pct = min(requested, self.budget.max_risk_per_trade_pct)
        risk_per_unit = abs(entry - stop)
        if risk_per_unit <= 0:
            return 0.0
        qty = (equity_v * risk_pct) / risk_per_unit
        # Cap notional by asset concentration
        qty = min(qty, (equity_v * self.budget.max_asset_concentration_pct) / entry)
        return _finite_size(qty)

    def calculate_volatility_size(
        self,
        equity: float,
        entry_price: float,
        atr: float,
        atr_multiplier: float = 2.0,
        target_vol_pct: float = 0.015
    ) -> float:
        """
        Volatility-based sizing targeting specific portfolio volatility contribution.
        """
        equity_v = positive_float(equity)
        entry = positive_float(entry_price)
        atr_v = positive_float(atr)
        multiplier = positive_float(atr_multiplier)
        target = positive_float(target_vol_pct)
        if equity_v is None or entry is None or atr_v is None or multiplier is None or target is None:
            return 0.0
        qty = (equity_v * target) / (atr_v * multiplier)
        qty = min(qty, (equity_v * self.budget.max_asset_concentration_pct) / entry)
        return _finite_size(qty)

    def calculate_kelly_size(
        self,
        equity: float,
        entry_price: float,
        win_rate: float,
        profit_factor: float,
        fraction: float = 0.5  # Half-Kelly for safety
    ) -> float:
        """
        Kelly Criterion Sizing: f* = (p * (b + 1) - 1) / b
        where p = win rate (0..1), b = win/loss payoff ratio (approximated from profit factor).
        """
        equity_v = positive_float(equity)
        entry = positive_float(entry_price)
        p_raw = positive_float(win_rate)
        b_raw = positive_float(profit_factor)
        scale = positive_float(fraction)
        if equity_v is None or entry is None or p_raw is None or b_raw is None or scale is None:
            return 0.0
        if p_raw > 1.0:
            # e.g. 58 (percent) passed where a fraction was expected: refusing
            # beats clamping to the maximum-confidence bet.
            return 0.0
        p = max(0.01, min(0.99, p_raw))
        b = max(0.1, b_raw)
        kelly_fraction = (p * (b + 1.0) - 1.0) / b
        if not kelly_fraction > 0:
            return 0.0  # Negative edge -> No trade

        # Scale by conservative factor (Half Kelly, never above full Kelly) and budget ceiling
        scaled_fraction = min(kelly_fraction * min(scale, 1.0), self.budget.max_risk_per_trade_pct)
        return _finite_size((equity_v * scaled_fraction) / entry)

    def calculate_risk_parity_weights(self, volatilities: dict[str, float]) -> dict[str, float]:
        """
        Inverse volatility risk parity weight allocation.
        Weight_i = (1 / Vol_i) / Sum(1 / Vol_j).
        """
        if not volatilities:
            return {}
        invalid = sorted(str(k) for k, v in volatilities.items() if positive_float(v) is None)
        if invalid:
            # A zero/negative/NaN volatility is a data error; clamping it to
            # 1e-6 handed that asset ~100% of the weight.
            raise ValueError(f"volatilities must be finite and > 0; invalid for {invalid}")
        inv_vols = {k: 1.0 / float(v) for k, v in volatilities.items()}
        total_inv_vol = sum(inv_vols.values())
        return {k: round(v / total_inv_vol, 4) for k, v in inv_vols.items()}

    def compute_var_cvar(
        self,
        returns: list[float],
        confidence_level: float = 0.95,
        portfolio_value: float = 10000.0
    ) -> tuple[float, float, float, float]:
        """
        Computes Historical Value at Risk (VaR) and Conditional VaR (Expected Shortfall).
        Returns: (var_pct, var_dollar, cvar_pct, cvar_dollar)
        """
        confidence = finite_float(confidence_level)
        if confidence is None or not 0.5 <= confidence < 1.0:
            raise ValueError(f"confidence_level must be within [0.5, 1.0), got {confidence_level!r}")
        if positive_float(portfolio_value) is None:
            raise ValueError(f"portfolio_value must be finite and > 0, got {portfolio_value!r}")
        clean = finite_values(returns or [])
        if len(clean) < 10:
            # Not enough measured returns: report "unknown", not "zero risk".
            return math.nan, math.nan, math.nan, math.nan

        arr = np.array(clean)
        alpha = (1.0 - confidence) * 100.0
        var_pct = abs(float(np.percentile(arr, alpha)))
        tail_losses = arr[arr <= -var_pct]
        cvar_pct = abs(float(np.mean(tail_losses))) if len(tail_losses) > 0 else var_pct

        var_dollar = round(portfolio_value * var_pct, 2)
        cvar_dollar = round(portfolio_value * cvar_pct, 2)
        return round(var_pct * 100.0, 2), var_dollar, round(cvar_pct * 100.0, 2), cvar_dollar

    def run_stress_tests(self, portfolio_notional: float) -> dict[str, float]:
        """
        Calculates impact of predefined market shock scenarios.
        """
        scenarios = {
            "FLASH_CRASH_10PCT": -0.10,
            "BLACK_SWAN_25PCT": -0.25,
            "HIGH_VOL_SPIKE_5PCT": -0.05,
            "CORRELATION_BREAKDOWN_8PCT": -0.08
        }
        notional = finite_float(portfolio_notional)
        if notional is None:
            raise ValueError(f"portfolio_notional must be a finite number, got {portfolio_notional!r}")
        return {
            k: round(notional * shock, 2) for k, shock in scenarios.items()
        }

    def adjust_size_for_drawdown(
        self,
        base_size: float,
        current_drawdown_pct: float
    ) -> tuple[float, float]:
        """
        Dynamically scales down position sizes as account drawdown increases:
        - 0% - 5% DD: 100% sizing
        - 5% - 10% DD: Linear reduction from 100% to 50% sizing
        - 10% - 15% DD: 25% sizing
        - >= 15% DD: 0% sizing (Trading halted by circuit breaker)
        """
        size = finite_float(base_size)
        dd = finite_float(current_drawdown_pct)
        if size is None or size <= 0 or dd is None:
            # Unknown drawdown or size: halt sizing rather than trade at 100%.
            return 0.0, 0.0
        if dd >= 15.0:
            multiplier = 0.0
        elif dd >= 10.0:
            multiplier = 0.25
        elif dd >= 5.0:
            multiplier = 1.0 - ((dd - 5.0) / 5.0) * 0.5  # 1.0 down to 0.5
        else:
            multiplier = 1.0
        
        adjusted_size = base_size * multiplier
        return round(adjusted_size, 6), round(multiplier, 2)


def _finite_size(qty: float) -> float:
    """Round a computed size; anything non-finite or negative becomes 0.0."""
    if not math.isfinite(qty) or qty <= 0:
        return 0.0
    return float(round(qty, 6))
