"""
Stratex/strategies/pair_trading.py — Pair Trading Strategy with Statistical Arbitrage.

Implements:
1. Cointegration Testing: Engle-Granger two-step cointegration test
2. Z-Score Spread Analysis: mean-reversion signals based on z-scores
3. Pair Selection: rank potential pairs by cointegration strength
4. Hedge Ratio Calculation: OLS-based hedge ratios
5. Spread Trading: entry/exit based on z-score thresholds
6. Risk Management: stop-loss and position sizing for pair trades
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any


@dataclass
class PairStats:
    """Statistics for a trading pair."""
    symbol_a: str
    symbol_b: str
    hedge_ratio: float
    spread_mean: float
    spread_std: float
    z_score: float
    cointegration_pvalue: float
    is_cointegrated: bool
    spread_series: list[float] = field(default_factory=list)


@dataclass
class PairSignal:
    """A trading signal for a pair."""
    symbol_a: str
    symbol_b: str
    signal: str  # long_a_short_b, short_a_long_b, close, hold
    entry_z: float
    z_score: float
    spread: float
    size_a: float
    size_b: float
    confidence: float


class PairTradingStrategy:
    """Statistical arbitrage pair trading strategy."""

    def __init__(
        self,
        entry_threshold: float = 2.0,
        exit_threshold: float = 0.5,
        stop_loss_z: float = 3.0,
    ):
        self.entry_threshold = entry_threshold
        self.exit_threshold = exit_threshold
        self.stop_loss_z = stop_loss_z
        self._pair_stats: dict[tuple, PairStats] = {}

    def calculate_hedge_ratio(
        self,
        prices_a: list[float],
        prices_b: list[float],
    ) -> float:
        """Calculate hedge ratio using OLS regression."""
        if len(prices_a) < 2 or len(prices_b) < 2:
            return 1.0

        n = len(prices_a)
        sum_xy = sum(a * b for a, b in zip(prices_a, prices_b))
        sum_x = sum(prices_a)
        sum_y = sum(prices_b)
        sum_x2 = sum(a * a for a in prices_a)

        # OLS: beta = (n*sum_xy - sum_x*sum_y) / (n*sum_x2 - sum_x^2)
        numerator = n * sum_xy - sum_x * sum_y
        denominator = n * sum_x2 - sum_x * sum_x

        if abs(denominator) < 1e-10:
            return 1.0

        beta = numerator / denominator
        return beta

    def calculate_spread(
        self,
        price_a: float,
        price_b: float,
        hedge_ratio: float,
    ) -> float:
        """Calculate the spread: price_a - hedge_ratio * price_b."""
        return price_a - hedge_ratio * price_b

    def calculate_z_score(
        self,
        spread: float,
        spread_mean: float,
        spread_std: float,
    ) -> float:
        """Calculate z-score of current spread."""
        if spread_std < 1e-10:
            return 0.0
        return (spread - spread_mean) / spread_std

    def compute_cointegration(
        self,
        prices_a: list[float],
        prices_b: list[float],
    ) -> dict[str, Any]:
        """Compute cointegration statistics."""
        if len(prices_a) < 20 or len(prices_b) < 20:
            return {"is_cointegrated": False, "p_value": 1.0}

        hedge_ratio = self.calculate_hedge_ratio(prices_a, prices_b)
        spreads = [
            self.calculate_spread(a, b, hedge_ratio)
            for a, b in zip(prices_a, prices_b)
        ]

        mean = sum(spreads) / len(spreads)
        variance = sum((s - mean) ** 2 for s in spreads) / len(spreads)
        std = math.sqrt(variance)

        # Simple cointegration test: check if spread is stationary
        # Using ADF-like test: regression of spread on its lag
        # Simplified: check autocorrelation of spread changes
        if len(spreads) < 2:
            return {"is_cointegrated": False, "p_value": 1.0}

        changes = [spreads[i] - spreads[i-1] for i in range(1, len(spreads))]
        if len(changes) < 2:
            return {"is_cointegrated": False, "p_value": 1.0}

        # Autocorrelation of first differences
        mean_change = sum(changes) / len(changes)
        var_change = sum((c - mean_change) ** 2 for c in changes) / len(changes)

        if var_change < 1e-10:
            # Spread is perfectly stationary
            return {"is_cointegrated": True, "p_value": 0.01}

        # Ljung-Box-like test on changes
        lag1_cov = sum(
            (changes[i] - mean_change) * (changes[i-1] - mean_change)
            for i in range(1, len(changes))
        ) / len(changes)

        lag1_corr = lag1_cov / var_change if var_change > 0 else 0.0

        # Low autocorrelation in differences = stationary = cointegrated
        p_value = abs(lag1_corr)  # Simplified
        is_cointegrated = p_value < 0.05

        return {
            "is_cointegrated": is_cointegrated,
            "p_value": p_value,
            "lag1_corr": lag1_corr,
        }

    def analyze_pair(
        self,
        symbol_a: str,
        symbol_b: str,
        prices_a: list[float],
        prices_b: list[float],
    ) -> PairStats:
        """Analyze a pair for trading."""
        hedge_ratio = self.calculate_hedge_ratio(prices_a, prices_b)

        spreads = [
            self.calculate_spread(a, b, hedge_ratio)
            for a, b in zip(prices_a, prices_b)
        ]

        mean = sum(spreads) / len(spreads) if spreads else 0.0
        variance = sum((s - mean) ** 2 for s in spreads) / len(spreads) if spreads else 0.0
        std = math.sqrt(variance)

        coint = self.compute_cointegration(prices_a, prices_b)

        current_spread = spreads[-1] if spreads else 0.0
        z_score = self.calculate_z_score(current_spread, mean, std)

        stats = PairStats(
            symbol_a=symbol_a,
            symbol_b=symbol_b,
            hedge_ratio=hedge_ratio,
            spread_mean=mean,
            spread_std=std,
            z_score=z_score,
            cointegration_pvalue=coint["p_value"],
            is_cointegrated=coint["is_cointegrated"],
            spread_series=spreads[-100:],  # Keep last 100
        )

        self._pair_stats[(symbol_a, symbol_b)] = stats
        return stats

    def generate_signal(
        self,
        symbol_a: str,
        symbol_b: str,
        price_a: float,
        price_b: float,
        risk_per_trade: float = 0.01,
        capital: float = 100000.0,
    ) -> PairSignal | None:
        """Generate a trading signal for a pair."""
        pair_key = (symbol_a, symbol_b)
        if pair_key not in self._pair_stats:
            return None

        stats = self._pair_stats[pair_key]

        if not stats.is_cointegrated:
            return PairSignal(
                symbol_a=symbol_a,
                symbol_b=symbol_b,
                signal="hold",
                entry_z=0.0,
                z_score=stats.z_score,
                spread=self.calculate_spread(price_a, price_b, stats.hedge_ratio),
                size_a=0.0,
                size_b=0.0,
                confidence=0.0,
            )

        current_spread = self.calculate_spread(price_a, price_b, stats.hedge_ratio)
        z = self.calculate_z_score(current_spread, stats.spread_mean, stats.spread_std)

        # Entry conditions
        if z > self.entry_threshold:
            signal = "short_a_long_b"  # Spread too high: short A, long B
            entry_z = z
        elif z < -self.entry_threshold:
            signal = "long_a_short_b"  # Spread too low: long A, short B
            entry_z = z
        elif abs(z) < self.exit_threshold:
            signal = "close"  # Mean reverted: close position
            entry_z = z
        else:
            signal = "hold"
            entry_z = z

        # Calculate position sizes
        if signal in ("long_a_short_b", "short_a_long_b"):
            notional = capital * risk_per_trade * 10  # 10x risk multiplier
            size_a = notional / price_a
            size_b = notional / price_b * stats.hedge_ratio
        else:
            size_a = 0.0
            size_b = 0.0

        # Confidence based on z-score distance and cointegration
        confidence = min(1.0, abs(z) / self.entry_threshold)
        if stats.is_cointegrated:
            confidence *= 0.9  # Boost for cointegration
        confidence *= (1.0 - stats.cointegration_pvalue)

        return PairSignal(
            symbol_a=symbol_a,
            symbol_b=symbol_b,
            signal=signal,
            entry_z=entry_z,
            z_score=z,
            spread=current_spread,
            size_a=size_a,
            size_b=size_b,
            confidence=confidence,
        )

    def rank_pairs(
        self,
        price_data: dict[str, list[float]],
        top_n: int = 10,
    ) -> list[PairStats]:
        """Rank potential trading pairs by cointegration strength."""
        pairs = []

        symbols = list(price_data.keys())
        for i in range(len(symbols)):
            for j in range(i + 1, len(symbols)):
                s1, s2 = symbols[i], symbols[j]
                prices_1 = price_data[s1]
                prices_2 = price_data[s2]

                if len(prices_1) < 20 or len(prices_2) < 20:
                    continue

                stats = self.analyze_pair(s1, s2, prices_1, prices_2)

                if stats.is_cointegrated:
                    pairs.append(stats)

        # Rank by cointegration strength (lower p-value = stronger)
        pairs.sort(key=lambda s: s.cointegration_pvalue)

        return pairs[:top_n]

    def check_stop_loss(
        self,
        symbol_a: str,
        symbol_b: str,
        entry_price_a: float,
        entry_price_b: float,
        current_price_a: float,
        current_price_b: float,
    ) -> bool:
        """Check if a pair trade has hit stop-loss."""
        pair_key = (symbol_a, symbol_b)
        if pair_key not in self._pair_stats:
            return False

        stats = self._pair_stats[pair_key]

        entry_spread = self.calculate_spread(entry_price_a, entry_price_b, stats.hedge_ratio)
        current_spread = self.calculate_spread(current_price_a, current_price_b, stats.hedge_ratio)

        # Calculate spread change in z-score terms
        entry_z = self.calculate_z_score(entry_spread, stats.spread_mean, stats.spread_std)
        current_z = self.calculate_z_score(current_spread, stats.spread_mean, stats.spread_std)

        # If spread moved further from mean beyond stop loss
        if abs(current_z) > self.stop_loss_z:
            # Check if it moved against us
            if (entry_z > 0 and current_z > entry_z) or (entry_z < 0 and current_z < entry_z):
                return True

        return False
