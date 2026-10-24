"""
Stratex/strategies/options_pricing.py — Options Pricing Models & Strategy.

Implements:
1. Black-Scholes Model: European options pricing
2. Binomial Tree Model: American options pricing
3. Greeks Calculation: Delta, Gamma, Theta, Vega, Rho
4. Option Strategy Analysis: analyze complex option strategies
5. Implied Volatility: calculate IV from market prices
6. Volatility Smile: model the volatility smile/skew
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any


@dataclass
class OptionContract:
    """An options contract."""
    symbol: str
    option_type: str  # call, put
    strike_price: float
    expiry: float  # days to expiry
    current_price: float  # underlying price
    risk_free_rate: float = 0.02
    dividend_yield: float = 0.0


@dataclass
class OptionGreeks:
    """Option Greeks."""
    delta: float
    gamma: float
    theta: float
    vega: float
    rho: float
    iv: float  # implied volatility


@dataclass
class OptionPrice:
    """Calculated option price."""
    call_price: float
    put_price: float
    model: str
    greeks: OptionGreeks


@dataclass
class StrategyLeg:
    """A leg in an option strategy."""
    contract: OptionContract
    action: str  # buy, sell
    quantity: int


@dataclass
class StrategyAnalysis:
    """Analysis of an option strategy."""
    strategy_name: str
    max_profit: float
    max_loss: float
    breakeven_points: list[float]
    risk_reward_ratio: float
    prob_profit: float
    description: str


class OptionsPricing:
    """Options pricing and analysis engine."""

    def __init__(self):
        self._prices: dict[str, OptionPrice] = {}

    # ── Black-Scholes Model ──────────────────────────────────────────────────

    def black_scholes(
        self,
        contract: OptionContract,
        volatility: float = 0.2,
    ) -> OptionPrice:
        """Price an option using Black-Scholes model."""
        S = contract.current_price
        K = contract.strike_price
        T = contract.expiry / 365.0
        r = contract.risk_free_rate
        q = contract.dividend_yield
        sigma = volatility

        # d1 and d2
        d1 = (math.log(S / K) + (r - q + 0.5 * sigma ** 2) * T) / (sigma * math.sqrt(T))
        d2 = d1 - sigma * math.sqrt(T)

        # N(d1) and N(d2)
        Nd1 = self._norm_cdf(d1)
        Nd2 = self._norm_cdf(d2)

        # Call price
        call = S * math.exp(-q * T) * Nd1 - K * math.exp(-r * T) * Nd2

        # Put price
        put = K * math.exp(-r * T) * self._norm_cdf(-d2) - S * math.exp(-q * T) * self._norm_cdf(-d1)

        # Greeks
        delta_call = math.exp(-q * T) * Nd1
        delta_put = math.exp(-q * T) * (Nd1 - 1)

        gamma = math.exp(-q * T) * self._norm_pdf(d1) / (S * sigma * math.sqrt(T))

        theta_call = (
            -(S * math.exp(-q * T) * self._norm_pdf(d1) * sigma) / (2 * math.sqrt(T))
            - r * K * math.exp(-r * T) * Nd2
            + q * S * math.exp(-q * T) * Nd1
        ) / 365

        theta_put = (
            -(S * math.exp(-q * T) * self._norm_pdf(d1) * sigma) / (2 * math.sqrt(T))
            + r * K * math.exp(-r * T) * self._norm_cdf(-d2)
            - q * S * math.exp(-q * T) * self._norm_cdf(-d1)
        ) / 365

        vega = S * math.exp(-q * T) * self._norm_pdf(d1) * math.sqrt(T) / 100

        rho_call = K * T * math.exp(-r * T) * Nd2 / 100
        rho_put = -K * T * math.exp(-r * T) * self._norm_cdf(-d2) / 100

        # Use call delta for overall (simplified)
        greeks = OptionGreeks(
            delta=delta_call if contract.option_type == "call" else delta_put,
            gamma=gamma,
            theta=theta_call if contract.option_type == "call" else theta_put,
            vega=vega,
            rho=rho_call if contract.option_type == "call" else rho_put,
            iv=sigma,
        )

        price = OptionPrice(
            call_price=call,
            put_price=put,
            model="black_scholes",
            greeks=greeks,
        )

        self._prices[f"{contract.symbol}_{contract.option_type}_{contract.strike_price}"] = price
        return price

    def _norm_cdf(self, x: float) -> float:
        """Normal cumulative distribution function."""
        return (1.0 + math.erf(x / math.sqrt(2))) / 2.0

    def _norm_pdf(self, x: float) -> float:
        """Normal probability density function."""
        return math.exp(-x ** 2 / 2) / math.sqrt(2 * math.pi)

    # ── Binomial Tree Model ─────────────────────────────────────────────────

    def binomial_tree(
        self,
        contract: OptionContract,
        volatility: float = 0.2,
        steps: int = 100,
    ) -> OptionPrice:
        """Price an option using binomial tree model."""
        S = contract.current_price
        K = contract.strike_price
        T = contract.expiry / 365.0
        r = contract.risk_free_rate
        sigma = volatility

        dt = T / steps
        u = math.exp(sigma * math.sqrt(dt))
        d = 1 / u
        p = (math.exp((r - 0.5 * sigma ** 2) * dt) - d) / (u - d)
        discount = math.exp(-r * dt)

        # Build stock price tree
        ST = []
        for i in range(steps + 1):
            ST.append(S * (u ** i) * (d ** (steps - i)))

        # Build option value tree (backward induction)
        call_values = []
        for i in range(steps + 1):
            if contract.option_type == "call":
                call_values.append(max(ST[i] - K, 0))
            else:
                call_values.append(max(K - ST[i], 0))

        # Backward induction
        for step in range(steps - 1, -1, -1):
            for i in range(step + 1):
                call_values[i] = discount * (
                    p * call_values[i + 1] + (1 - p) * call_values[i]
                )

        # American option: check for early exercise
        for step in range(steps - 1, -1, -1):
            for i in range(step + 1):
                S_curr = S * (u ** i) * (d ** (step - i))
                intrinsic = max(S_curr - K, 0) if contract.option_type == "call" else max(K - S_curr, 0)
                call_values[i] = max(call_values[i], intrinsic)

        # Calculate approximate Greeks
        delta = (self._binomial_delta(S, K, T, r, sigma, steps, contract.option_type, u, d, p, discount))

        price = OptionPrice(
            call_price=call_values[0],
            put_price=call_values[0] if contract.option_type == "put" else 0,
            model="binomial",
            greeks=OptionGreeks(
                delta=delta,
                gamma=0.0,
                theta=0.0,
                vega=0.0,
                rho=0.0,
                iv=sigma,
            ),
        )

        return price

    def _binomial_delta(
        self, S, K, T, r, sigma, steps, option_type, u, d, p, discount
    ) -> float:
        """Calculate delta from binomial tree."""
        dt = T / steps

        # Up and down prices after one step
        S_up = S * u
        S_down = S * d

        # Option values after one step
        T_up = S_up - K if option_type == "call" else max(K - S_up, 0)
        T_down = S_down - K if option_type == "call" else max(K - S_down, 0)

        # Delta
        delta = (T_up - T_down) / (S_up - S_down)

        return delta

    # ── Implied Volatility ──────────────────────────────────────────────────

    def calculate_iv(
        self,
        contract: OptionContract,
        market_price: float,
    ) -> float:
        """Calculate implied volatility from market price."""
        # Newton-Raphson method
        vol = 0.2  # Initial guess
        tolerance = 0.001
        max_iterations = 100

        for _ in range(max_iterations):
            price = self.black_scholes(contract, vol)
            target = market_price if contract.option_type == "call" else market_price

            actual = price.call_price if contract.option_type == "call" else price.put_price

            error = actual - target

            if abs(error) < tolerance:
                return vol

            # Calculate vega for Newton step
            vega = price.greeks.vega * 100  # Vega is per 1% change

            if abs(vega) < 1e-10:
                break

            vol = vol - error / vega
            vol = max(0.01, min(3.0, vol))

        return vol

    # ── Strategy Analysis ───────────────────────────────────────────────────

    def analyze_strategy(
        self,
        strategy_name: str,
        legs: list[StrategyLeg],
        underlying_price: float,
    ) -> StrategyAnalysis:
        """Analyze an option strategy."""
        # Calculate net debit/credit
        net_debit = 0.0
        max_profit = 0.0
        max_loss = 0.0

        for leg in legs:
            price = self.black_scholes(leg.contract)
            premium = price.call_price if leg.contract.option_type == "call" else price.put_price

            if leg.action == "buy":
                net_debit += premium * leg.quantity
            else:
                net_debit -= premium * leg.quantity

        # Analyze common strategies
        if strategy_name.lower() == "long_call":
            max_profit = float("inf")
            max_loss = net_debit
            breakeven = underlying_price + net_debit
            description = "Bullish strategy - profit from price increase"

        elif strategy_name.lower() == "long_put":
            max_profit = underlying_price - net_debit
            max_loss = net_debit
            breakeven = underlying_price - net_debit
            description = "Bearish strategy - profit from price decrease"

        elif strategy_name.lower() == "covered_call":
            max_profit = 0.0
            max_loss = 0.0
            description = "Income strategy - write call against owned stock"

        elif strategy_name.lower() == "protective_put":
            max_loss = net_debit
            description = "Insurance strategy - buy put to protect long position"

        else:
            description = f"Strategy: {strategy_name}"

        risk_reward = max_profit / max(1.0, abs(max_loss))

        return StrategyAnalysis(
            strategy_name=strategy_name,
            max_profit=max_profit,
            max_loss=max_loss,
            breakeven_points=[underlying_price + net_debit],
            risk_reward_ratio=risk_reward,
            prob_profit=0.5,
            description=description,
        )

    # ── Volatility Smile ─────────────────────────────────────────────────────

    def build_volatility_smile(
        self,
        contract: OptionContract,
        strikes: list[float],
        prices: list[float],
    ) -> dict[str, Any]:
        """Build volatility smile from option prices."""
        smile = []

        for strike, price in zip(strikes, prices):
            contract.strike_price = strike
            iv = self.calculate_iv(contract, price)
            smile.append({
                "strike": strike,
                "price": price,
                "iv": iv,
            })

        # Calculate smile metrics
        if len(smile) >= 2:
            min_iv = min(s["iv"] for s in smile)
            max_iv = max(s["iv"] for s in smile)
            skew = (smile[-1]["iv"] - smile[0]["iv"]) / max(1e-10, smile[len(smile)//2]["iv"])
        else:
            min_iv = max_iv = skew = 0.0

        return {
            "smile": smile,
            "min_iv": min_iv,
            "max_iv": max_iv,
            "skew": skew,
            "atm_iv": smile[len(smile)//2]["iv"] if smile else 0.0,
        }

    # ── Reporting ──────────────────────────────────────────────────────────

    def get_options_report(self) -> dict[str, Any]:
        """Generate an options pricing report."""
        return {
            "total_prices_calculated": len(self._prices),
            "models": ["black_scholes", "binomial"],
            "strategies": ["long_call", "long_put", "covered_call", "protective_put"],
        }
