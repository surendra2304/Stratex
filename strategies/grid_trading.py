"""
Stratex/strategies/grid_trading.py — Grid Trading & Mean Reversion Strategy.

Implements:
1. Grid Trading: place buy/sell grids at regular intervals
2. Mean Reversion: trade deviation from moving average
3. Adaptive Grid: dynamically adjust grid based on volatility
4. Grid Profit Calculation: calculate expected profit from grid
5. Position Sizing: risk-based grid position sizing
6. Grid Management: add/remove/modify grid levels dynamically
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any


@dataclass
class GridLevel:
    """A single grid level."""
    level: float
    order_type: str  # buy, sell
    size: float
    filled: bool = False
    profit: float = 0.0


@dataclass
class GridConfig:
    """Grid trading configuration."""
    symbol: str
    lower_bound: float
    upper_bound: float
    grid_count: int
    position_size: float
    volatility_multiplier: float = 1.0
    take_profit: float = 0.5  # Percent
    stop_loss: float = 2.0  # Percent


@dataclass
class GridResult:
    """Grid trading result."""
    symbol: str
    active_levels: int
    filled_levels: int
    total_profit: float
    profit_per_level: float
    win_rate: float
    efficiency: float
    recommendations: list[str]


class GridTradingStrategy:
    """Grid trading and mean reversion strategy."""

    def __init__(self):
        self._grids: dict[str, GridConfig] = {}
        self._active_levels: dict[str, list[GridLevel]] = {}
        self._results: dict[str, GridResult] = {}

    # ── Grid Setup ─────────────────────────────────────────────────────────

    def setup_grid(
        self,
        symbol: str,
        current_price: float,
        upper_bound: float | None = None,
        lower_bound: float | None = None,
        grid_count: int = 10,
        position_size: float = 0.01,
        volatility: float = 0.02,
    ) -> GridConfig:
        """Set up a grid trading configuration."""
        if upper_bound is None:
            upper_bound = current_price * (1 + volatility * 3)
        if lower_bound is None:
            lower_bound = current_price * (1 - volatility * 3)

        config = GridConfig(
            symbol=symbol,
            lower_bound=lower_bound,
            upper_bound=upper_bound,
            grid_count=grid_count,
            position_size=position_size,
            volatility_multiplier=volatility,
        )

        self._grids[symbol] = config

        # Generate grid levels
        levels = self._generate_levels(config)
        self._active_levels[symbol] = levels

        return config

    def _generate_levels(self, config: GridConfig) -> list[GridLevel]:
        """Generate grid levels."""
        step = (config.upper_bound - config.lower_bound) / config.grid_count

        levels = []
        for i in range(config.grid_count + 1):
            price = config.lower_bound + step * i
            order_type = "buy" if i % 2 == 0 else "sell"

            # Size increases toward center
            dist_from_center = abs(i - config.grid_count / 2)
            size_multiplier = 1.0 + (1.0 - dist_from_center / (config.grid_count / 2)) * 0.5

            level = GridLevel(
                level=price,
                order_type=order_type,
                size=config.position_size * size_multiplier,
            )
            levels.append(level)

        return levels

    # ── Adaptive Grid ───────────────────────────────────────────────────────

    def adapt_grid(
        self,
        symbol: str,
        current_price: float,
        volatility: float,
    ) -> GridConfig | None:
        """Adapt grid based on current volatility."""
        if symbol not in self._grids:
            return None

        config = self._grids[symbol]

        # Adjust bounds based on volatility change
        old_volatility = config.volatility_multiplier
        new_volatility = max(0.005, volatility)

        # Recalculate bounds
        price_range = current_price * new_volatility * 3
        config.lower_bound = current_price - price_range
        config.upper_bound = current_price + price_range
        config.volatility_multiplier = new_volatility

        # Regenerate levels
        self._active_levels[symbol] = self._generate_levels(config)

        return config

    # ── Mean Reversion ─────────────────────────────────────────────────────

    def calculate_mean_reversion_signal(
        self,
        symbol: str,
        prices: list[float],
        z_threshold: float = 2.0,
    ) -> dict[str, Any]:
        """Calculate mean reversion signal."""
        if len(prices) < 20:
            return {"signal": "hold", "z_score": 0.0}

        # Calculate moving average
        window = min(20, len(prices))
        ma = sum(prices[-window:]) / window

        # Calculate standard deviation
        variance = sum((p - ma) ** 2 for p in prices[-window:]) / window
        std = math.sqrt(variance)

        # Z-score
        current = prices[-1]
        z_score = (current - ma) / std if std > 0 else 0.0

        # Signal
        if z_score > z_threshold:
            signal = "short"  # Price above mean, expect reversion down
        elif z_score < -z_threshold:
            signal = "long"  # Price below mean, expect reversion up
        else:
            signal = "hold"

        return {
            "symbol": symbol,
            "signal": signal,
            "z_score": z_score,
            "moving_average": ma,
            "current_price": current,
            "deviation": current - ma,
            "std_dev": std,
        }

    def calculate_bollinger_bands(
        self,
        prices: list[float],
        window: int = 20,
        num_std: float = 2.0,
    ) -> dict[str, float]:
        """Calculate Bollinger Bands."""
        if len(prices) < window:
            return {"upper": 0.0, "middle": 0.0, "lower": 0.0}

        recent = prices[-window:]
        ma = sum(recent) / window
        variance = sum((p - ma) ** 2 for p in recent) / window
        std = math.sqrt(variance)

        return {
            "upper": ma + num_std * std,
            "middle": ma,
            "lower": ma - num_std * std,
            "bandwidth": (ma + num_std * std - (ma - num_std * std)) / ma,
            "percent_b": (prices[-1] - (ma - num_std * std)) /
                         max(1e-10, (ma + num_std * std) - (ma - num_std * std)),
        }

    # ── Grid Execution ──────────────────────────────────────────────────────

    def execute_grid_check(
        self,
        symbol: str,
        current_price: float,
    ) -> list[GridLevel]:
        """Check which grid levels should be executed."""
        if symbol not in self._active_levels:
            return []

        levels = self._active_levels[symbol]
        executed = []

        for level in levels:
            if level.filled:
                continue

            # Buy level: execute if current price <= level price
            if level.order_type == "buy" and current_price <= level.level:
                level.filled = True
                executed.append(level)

            # Sell level: execute if current price >= level price
            elif level.order_type == "sell" and current_price >= level.level:
                level.filled = True
                level.profit = (current_price - level.level) / level.level * 100
                executed.append(level)

        return executed

    # ── Grid Profit Calculation ─────────────────────────────────────────────

    def calculate_grid_profit(self, symbol: str) -> GridResult:
        """Calculate profit from grid trading."""
        if symbol not in self._active_levels:
            return GridResult(
                symbol=symbol,
                active_levels=0,
                filled_levels=0,
                total_profit=0.0,
                profit_per_level=0.0,
                win_rate=0.0,
                efficiency=0.0,
                recommendations=["Grid not set up"],
            )

        levels = self._active_levels[symbol]
        filled = [l for l in levels if l.filled]
        total_profit = sum(l.profit for l in filled)

        win_rate = sum(1 for l in filled if l.profit > 0) / max(1, len(filled))

        efficiency = (
            len(filled) / len(levels) if levels else 0.0
        )

        recommendations = []
        if efficiency < 0.3:
            recommendations.append("Low fill rate. Consider wider grid spacing.")
        if win_rate < 0.5 and filled:
            recommendations.append("Low win rate. Check trend direction.")
        if total_profit < 0:
            recommendations.append("Negative profit. Consider adjusting grid bounds.")

        result = GridResult(
            symbol=symbol,
            active_levels=len(levels),
            filled_levels=len(filled),
            total_profit=total_profit,
            profit_per_level=total_profit / max(1, len(filled)),
            win_rate=win_rate,
            efficiency=efficiency,
            recommendations=recommendations,
        )

        self._results[symbol] = result
        return result

    # ── Position Sizing ─────────────────────────────────────────────────────

    def calculate_grid_size(
        self,
        capital: float,
        risk_per_trade: float,
        grid_count: int,
        volatility: float,
    ) -> float:
        """Calculate optimal grid position size."""
        # Total risk = capital * risk_per_trade
        total_risk = capital * risk_per_trade

        # Per-level risk
        per_level_risk = total_risk / grid_count

        # Position size adjusted by volatility
        vol_adjustment = 1.0 / (1.0 + volatility * 10)

        size = per_level_risk * vol_adjustment

        return size

    # ── Reporting ──────────────────────────────────────────────────────────

    def get_grid_report(self) -> dict[str, Any]:
        """Generate a grid trading report."""
        return {
            "total_grids": len(self._grids),
            "total_levels": sum(len(l) for l in self._active_levels.values()),
            "total_filled": sum(
                sum(1 for lv in levels if lv.filled)
                for levels in self._active_levels.values()
            ),
            "total_profit": sum(r.total_profit for r in self._results.values()),
            "symbols": list(self._grids.keys()),
        }
