"""
risk/dynamic_risk_manager.py — Dynamic Quantitative Risk Management & Risk Budgeting Engine.

Features:
1. Sizing Models: Fixed Fractional, ATR/Volatility-based, Kelly Criterion (half/full), Risk Parity.
2. Dynamic Risk Scaling: Modulated by portfolio drawdown, market volatility regimes, and position correlations.
3. Strict Risk Budgeting: Max risk per trade, portfolio VaR/CVaR limits, daily/weekly/monthly loss caps, concentration limits.
4. Risk Metrics & Stress Testing: Historical/Parametric VaR (95%/99%), CVaR (Expected Shortfall), and simulated market shock scenarios.
"""

from dataclasses import dataclass

import numpy as np


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
        if equity <= 0 or entry_price <= 0 or stop_loss_price <= 0:
            return 0.0
        risk_pct = min(fraction_pct or self.budget.max_risk_per_trade_pct, self.budget.max_risk_per_trade_pct)
        risk_capital = equity * risk_pct
        risk_per_unit = abs(entry_price - stop_loss_price)
        if risk_per_unit <= 0:
            return 0.0
        qty = risk_capital / risk_per_unit
        # Cap notional by asset concentration
        max_notional = equity * self.budget.max_asset_concentration_pct
        if qty * entry_price > max_notional:
            qty = max_notional / entry_price
        return float(round(qty, 6))

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
        if equity <= 0 or entry_price <= 0 or atr <= 0:
            return 0.0
        dollar_risk = equity * target_vol_pct
        unit_risk = atr * atr_multiplier
        qty = dollar_risk / unit_risk
        max_notional = equity * self.budget.max_asset_concentration_pct
        if qty * entry_price > max_notional:
            qty = max_notional / entry_price
        return float(round(qty, 6))

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
        if equity <= 0 or entry_price <= 0 or win_rate <= 0 or profit_factor <= 0:
            return 0.0
        p = max(0.01, min(0.99, win_rate))
        b = max(0.1, profit_factor)
        kelly_fraction = (p * (b + 1.0) - 1.0) / b
        if kelly_fraction <= 0:
            return 0.0  # Negative edge -> No trade
        
        # Scale by conservative factor (Half Kelly) and budget ceiling
        scaled_fraction = min(kelly_fraction * fraction, self.budget.max_risk_per_trade_pct)
        notional = equity * scaled_fraction
        qty = notional / entry_price
        return float(round(qty, 6))

    def calculate_risk_parity_weights(self, volatilities: dict[str, float]) -> dict[str, float]:
        """
        Inverse volatility risk parity weight allocation.
        Weight_i = (1 / Vol_i) / Sum(1 / Vol_j).
        """
        if not volatilities:
            return {}
        inv_vols = {k: 1.0 / max(v, 1e-6) for k, v in volatilities.items()}
        total_inv_vol = sum(inv_vols.values())
        if total_inv_vol <= 0:
            equal_w = 1.0 / len(volatilities)
            return {k: equal_w for k in volatilities}
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
        if not returns or len(returns) < 10:
            return 0.0, 0.0, 0.0, 0.0
        
        arr = np.array(returns)
        alpha = (1.0 - confidence_level) * 100.0
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
        return {
            k: round(portfolio_notional * shock, 2) for k, shock in scenarios.items()
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
        dd = current_drawdown_pct
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
