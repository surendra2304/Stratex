"""
Stratex/execution/options_pricing_engine.py — Stratex Options Pricing Engine.

Options pricing and analysis: Black-Scholes, Greeks calculation, implied
volatility estimation, option chain management, and volatility surface
analysis. Provides comprehensive options analytics for trading decisions.
"""
from __future__ import annotations

import hashlib
import math
import time
from dataclasses import dataclass, field
from typing import Any

from flask import Blueprint, jsonify, request

options_bp = Blueprint("options", __name__, url_prefix="/api/v1/trading/options")


def black_scholes(S, K, T, r, sigma, option_type="call"):
    """Black-Scholes option pricing model."""
    if T <= 0 or sigma <= 0:
        if option_type == "call":
            return max(0, S - K)
        else:
            return max(0, K - S)

    d1 = (math.log(S / K) + (r + sigma ** 2 / 2) * T) / (sigma * math.sqrt(T))
    d2 = d1 - sigma * math.sqrt(T)

    if option_type == "call":
        price = S * norm_cdf(d1) - K * math.exp(-r * T) * norm_cdf(d2)
    else:
        price = K * math.exp(-r * T) * norm_cdf(-d2) - S * norm_cdf(-d1)

    return price


def norm_cdf(x):
    """Standard normal cumulative distribution function."""
    return (1.0 + math.erf(x / math.sqrt(2.0))) / 2.0


def calculate_greeks(S, K, T, r, sigma, option_type="call"):
    """Calculate option Greeks."""
    if T <= 0 or sigma <= 0:
        return {"delta": 0.0, "gamma": 0.0, "theta": 0.0, "vega": 0.0, "rho": 0.0}

    d1 = (math.log(S / K) + (r + sigma ** 2 / 2) * T) / (sigma * math.sqrt(T))
    d2 = d1 - sigma * math.sqrt(T)

    delta = norm_cdf(d1) if option_type == "call" else norm_cdf(d1) - 1
    gamma = norm_pdf(d1) / (S * sigma * math.sqrt(T))
    theta = -(S * norm_pdf(d1) * sigma) / (2 * math.sqrt(T))
    if option_type == "call":
        theta -= r * K * math.exp(-r * T) * norm_cdf(d2)
    else:
        theta += r * K * math.exp(-r * T) * norm_cdf(-d2)
    theta /= 365  # Daily theta
    vega = S * norm_pdf(d1) * math.sqrt(T) / 100  # Per 1% change in vol
    rho = K * T * math.exp(-r * T) * norm_cdf(d2) / 100 if option_type == "call" else -K * T * math.exp(-r * T) * norm_cdf(-d2) / 100

    return {"delta": delta, "gamma": gamma, "theta": theta, "vega": vega, "rho": rho}


def norm_pdf(x):
    """Standard normal probability density function."""
    return math.exp(-x ** 2 / 2) / math.sqrt(2 * math.pi)


def implied_volatility(S, K, T, r, market_price, option_type="call"):
    """Calculate implied volatility using Newton-Raphson method."""
    sigma = 0.2  # Initial guess
    for _ in range(100):
        price = black_scholes(S, K, T, r, sigma, option_type)
        diff = price - market_price
        if abs(diff) < 0.0001:
            return sigma

        # Vega
        d1 = (math.log(S / K) + (r + sigma ** 2 / 2) * T) / (sigma * math.sqrt(T))
        vega = S * norm_pdf(d1) * math.sqrt(T)

        if abs(vega) < 0.0001:
            break

        sigma -= diff / vega

    return sigma


@dataclass
class OptionContract:
    """An options contract."""
    contract_id: str
    symbol: str
    strike_price: float
    expiry_days: int
    option_type: str = "call"  # call, put
    underlying_price: float = 0.0
    market_price: float = 0.0
    bid: float = 0.0
    ask: float = 0.0
    volume: int = 0
    open_interest: int = 0
    iv: float = 0.0
    intrinsic_value: float = 0.0
    extrinsic_value: float = 0.0


@dataclass
class OptionChain:
    """An option chain for a symbol."""
    symbol: str
    expiry_date: float = 0.0
    calls: list[dict] = field(default_factory=list)
    puts: list[dict] = field(default_factory=list)
    total_volume: int = 0
    max_pain: float = 0.0
    pc_ratio: float = 0.0  # Put/Call ratio


@dataclass
class VolatilitySurface:
    """Volatility surface data."""
    symbol: str
    spot_price: float = 0.0
    surfaces: list[dict] = field(default_factory=list)  # [{strike, expiry, iv}]
    skew: float = 0.0  # Volatility skew
    term_structure: str = "contango"  # contango, backwardation
    avg_iv: float = 0.0


@dataclass
class OptionsStrategy:
    """An options trading strategy."""
    strategy_id: str
    name: str
    symbol: str
    leg_count: int = 0
    risk_profile: str = "neutral"  # bullish, bearish, neutral, volatile
    max_profit: float = 0.0
    max_loss: float = 0.0
    breakeven_points: list[float] = field(default_factory=list)
    probability_of_profit: float = 0.0
    description: str = ""


@dataclass
class OptionsAnalytics:
    """Comprehensive options analytics."""
    symbol: str
    total_contracts: int = 0
    avg_iv: float = 0.0
    put_call_ratio: float = 0.0
    max_pain: float = 0.0
    vov: float = 0.0  # Volatility of volatility
    suggested_strategy: str = ""


class OptionsPricingEngine:
    """Options pricing and analysis engine."""

    def __init__(self):
        self._contracts: dict[str, OptionContract] = {}
        self._chains: dict[str, OptionChain] = {}
        self._surfaces: dict[str, VolatilitySurface] = {}
        self._strategies: list[OptionsStrategy] = []

    def create_contract(
        self,
        symbol: str,
        strike_price: float,
        expiry_days: int,
        option_type: str = "call",
        underlying_price: float = 100.0,
        market_price: float = 0.0,
    ) -> OptionContract:
        """Create an options contract."""
        contract = OptionContract(
            contract_id=hashlib.md5(
                f"option:{symbol}:{strike_price}:{expiry_days}:{option_type}:{time.time()}".encode()
            ).hexdigest()[:12],
            symbol=symbol,
            strike_price=strike_price,
            expiry_days=expiry_days,
            option_type=option_type,
            underlying_price=underlying_price,
            market_price=market_price,
            bid=market_price * 0.99,
            ask=market_price * 1.01,
            volume=100 + (ord(hashlib.md5(contract_id_str.encode()).hexdigest()[:1]) % 900),
            open_interest=200 + (ord(hashlib.md5(f"oi:{contract_id_str}".encode()).hexdigest()[:1]) % 1800),
        )

        # Calculate IV
        if market_price > 0:
            r = 0.05  # Risk-free rate
            T = expiry_days / 365
            contract.iv = implied_volatility(underlying_price, strike_price, T, r, market_price, option_type)

        # Calculate intrinsic and extrinsic value
        if option_type == "call":
            intrinsic = max(0, underlying_price - strike_price)
        else:
            intrinsic = max(0, strike_price - underlying_price)
        contract.intrinsic_value = intrinsic
        contract.extrinsic_value = max(0, market_price - intrinsic)

        self._contracts[contract.contract_id] = contract
        return contract

    def build_chain(self, symbol: str, expiry_days: int, underlying_price: float) -> OptionChain:
        """Build an option chain."""
        chain = OptionChain(
            expiry_date=time.time() + expiry_days * 86400,
        )

        # Generate calls and puts at various strikes
        for offset in range(-5, 6):
            strike = underlying_price + offset * (underlying_price * 0.05)

            # Call
            call = self.create_contract(symbol, strike, expiry_days, "call", underlying_price,
                market_price=black_scholes(underlying_price, strike, expiry_days / 365, 0.05, 0.25, "call"))
            chain.calls.append({
                "strike": strike,
                "price": call.market_price,
                "iv": call.iv,
                "volume": call.volume,
                "open_interest": call.open_interest,
            })

            # Put
            put = self.create_contract(symbol, strike, expiry_days, "put", underlying_price,
                market_price=black_scholes(underlying_price, strike, expiry_days / 365, 0.05, 0.25, "put"))
            chain.puts.append({
                "strike": strike,
                "price": put.market_price,
                "iv": put.iv,
                "volume": put.volume,
                "open_interest": put.open_interest,
            })

        chain.total_volume = sum(c["volume"] for c in chain.calls) + sum(p["volume"] for p in chain.puts)
        chain.pc_ratio = sum(p["volume"] for p in chain.puts) / max(1, sum(c["volume"] for c in chain.calls))

        # Calculate max pain
        pains = []
        for strike in [c["strike"] for c in chain.calls]:
            pain = sum(
                max(0, strike - c["strike"]) * c["volume"] + max(0, p["strike"] - strike) * p["volume"]
                for c in chain.calls for p in chain.puts
            )
            pains.append(pain)

        chain.max_pain = min(pains) if pains else 0

        self._chains[symbol] = chain
        return chain

    def build_surface(self, symbol: str, spot_price: float) -> VolatilitySurface:
        """Build a volatility surface."""
        surface = VolatilitySurface(
            symbol=symbol,
            spot_price=spot_price,
        )

        # Generate surface points
        for expiry_days in [7, 14, 30, 60, 90, 180]:
            for offset in range(-3, 4):
                strike = spot_price + offset * (spot_price * 0.05)
                T = expiry_days / 365
                iv = 0.25 + (ord(hashlib.md5(f"iv:{symbol}:{strike}:{expiry_days}".encode()).hexdigest()[:1]) % 20) / 100
                surface.surfaces.append({
                    "strike": strike,
                    "expiry_days": expiry_days,
                    "iv": iv,
                })

        surface.avg_iv = sum(s["iv"] for s in surface.surfaces) / len(surface.surfaces)

        # Calculate skew (difference between OTM put IV and OTM call IV)
        put_ivs = [s["iv"] for s in surface.surfaces if s["strike"] < spot_price and s["expiry_days"] == 30]
        call_ivs = [s["iv"] for s in surface.surfaces if s["strike"] > spot_price and s["expiry_days"] == 30]
        surface.skew = (sum(put_ivs) / len(put_ivs) - sum(call_ivs) / len(call_ivs)) if put_ivs and call_ivs else 0

        # Term structure
        iv_7d = next((s["iv"] for s in surface.surfaces if s["expiry_days"] == 7), 0.25)
        iv_180d = next((s["iv"] for s in surface.surfaces if s["expiry_days"] == 180), 0.25)
        surface.term_structure = "contango" if iv_180d > iv_7d else "backwardation"

        self._surfaces[symbol] = surface
        return surface

    def suggest_strategy(self, symbol: str, outlook: str, risk_tolerance: str = "moderate") -> OptionsStrategy:
        """Suggest an options strategy based on outlook."""
        underlying = 100.0
        days_to_expiry = 30

        if outlook == "bullish":
            name = "Long Call" if risk_tolerance == "aggressive" else "Covered Call"
            max_profit = underlying * 0.2
            max_loss = underlying * 0.1
            be = [underlying]
            pop = 0.65
        elif outlook == "bearish":
            name = "Long Put" if risk_tolerance == "aggressive" else "Protective Put"
            max_profit = underlying * 0.2
            max_loss = underlying * 0.1
            be = [underlying]
            pop = 0.60
        elif outlook == "volatile":
            name = "Long Straddle"
            max_profit = float("inf")
            max_loss = underlying * 0.15
            be = [underlying * 0.85, underlying * 1.15]
            pop = 0.50
        else:
            name = "Short Straddle" if risk_tolerance == "aggressive" else "Covered Strangle"
            max_profit = underlying * 0.1
            max_loss = float("inf")
            be = [underlying * 0.8, underlying * 1.2]
            pop = 0.55

        strategy = OptionsStrategy(
            strategy_id=hashlib.md5(
                f"strategy:{symbol}:{outlook}:{time.time()}".encode()
            ).hexdigest()[:12],
            name=name,
            symbol=symbol,
            leg_count=2,
            risk_profile=outlook,
            max_profit=max_profit,
            max_loss=max_loss,
            breakeven_points=be,
            probability_of_profit=pop,
            description=f"Recommended {name} strategy for {outlook} outlook",
        )

        self._strategies.append(strategy)
        return strategy

    def get_analytics(self, symbol: str) -> OptionsAnalytics:
        """Get comprehensive options analytics."""
        contracts = [c for c in self._contracts.values() if c.symbol == symbol]
        chain = self._chains.get(symbol)

        analytics = OptionsAnalytics(
            symbol=symbol,
            total_contracts=len(contracts),
            avg_iv=sum(c.iv for c in contracts) / max(1, len(contracts)),
            put_call_ratio=chain.pc_ratio if chain else 0,
            max_pain=chain.max_pain if chain else 0,
            vov=0.15 + (ord(hashlib.md5(f"vov:{symbol}".encode()).hexdigest()[:1]) % 10) / 100,
        )

        analytics.suggested_strategy = self.suggest_strategy(symbol, "neutral").name

        return analytics

    def get_report(self) -> dict:
        """Get options pricing engine report."""
        return {
            "total_contracts": len(self._contracts),
            "total_chains": len(self._chains),
            "total_surfaces": len(self._surfaces),
            "total_strategies": len(self._strategies),
            "symbols_tracked": list(set(c.symbol for c in self._contracts.values())),
            "avg_iv": sum(c.iv for c in self._contracts.values()) / max(1, len(self._contracts)),
            "recent_contracts": [
                {"symbol": c.symbol, "strike": c.strike_price, "type": c.option_type, "iv": c.iv, "price": c.market_price}
                for c in list(self._contracts.values())[-10:]
            ],
            "recent_strategies": [
                {"symbol": s.symbol, "name": s.name, "profile": s.risk_profile, "pop": s.probability_of_profit}
                for s in self._strategies[-5:]
            ],
        }


options_engine = OptionsPricingEngine()


@options_bp.route("/contracts", methods=["POST"])
def create_contract():
    """Create an options contract."""
    data = request.get_json() or {}
    contract = options_engine.create_contract(
        symbol=data["symbol"],
        strike_price=data["strike_price"],
        expiry_days=data["expiry_days"],
        option_type=data.get("option_type", "call"),
        underlying_price=data.get("underlying_price", 100.0),
        market_price=data.get("market_price", 0.0),
    )
    return jsonify({
        "contract_id": contract.contract_id,
        "symbol": contract.symbol,
        "iv": contract.iv,
        "price": contract.market_price,
    })


@options_bp.route("/chains/{symbol}", methods=["POST"])
def build_chain(symbol):
    """Build an option chain."""
    data = request.get_json() or {}
    chain = options_engine.build_chain(
        symbol=symbol,
        expiry_days=data.get("expiry_days", 30),
        underlying_price=data.get("underlying_price", 100.0),
    )
    return jsonify({
        "symbol": chain.symbol,
        "calls": len(chain.calls),
        "puts": len(chain.puts),
        "pc_ratio": chain.pc_ratio,
        "max_pain": chain.max_pain,
    })


@options_bp.route("/surface/{symbol}", methods=["POST"])
def build_surface(symbol):
    """Build a volatility surface."""
    data = request.get_json() or {}
    surface = options_engine.build_surface(
        symbol=symbol,
        spot_price=data.get("spot_price", 100.0),
    )
    return jsonify({
        "symbol": surface.symbol,
        "avg_iv": surface.avg_iv,
        "skew": surface.skew,
        "term_structure": surface.term_structure,
    })


@options_bp.route("/strategies", methods=["POST"])
def suggest_strategy():
    """Suggest an options strategy."""
    data = request.get_json() or {}
    strategy = options_engine.suggest_strategy(
        symbol=data["symbol"],
        outlook=data["outlook"],
        risk_tolerance=data.get("risk_tolerance", "moderate"),
    )
    return jsonify({
        "strategy_id": strategy.strategy_id,
        "name": strategy.name,
        "max_profit": strategy.max_profit,
        "max_loss": strategy.max_loss,
        "probability_of_profit": strategy.probability_of_profit,
    })


@options_bp.route("/report", methods=["GET"])
def get_report():
    """Get options pricing engine report."""
    return jsonify(options_engine.get_report())
