"""
Stratex — Vectorbt Engine

Vectorized backtesting research engine. Fast vectorized operations on
financial data, portfolio simulation, signal generation, position sizing,
and metric calculation.

Source: https://github.com/polakowo/vectorbt
Tests: tests/test_real_batch4b.py
"""

from __future__ import annotations

import math
import random
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any


@dataclass
class SignalGenerator:
    """Vectorized signal generation."""
    @staticmethod
    def sma_signal(close: list[float], short_window: int = 20, long_window: int = 50) -> list[int]:
        signals = []
        for i in range(len(close)):
            if i >= long_window - 1:
                short_sma = sum(close[i-short_window+1:i+1]) / short_window
                long_sma = sum(close[i-long_window+1:i+1]) / long_window
                if short_sma > long_sma:
                    signals.append(1)
                elif short_sma < long_sma:
                    signals.append(-1)
                else:
                    signals.append(0)
            else:
                signals.append(0)
        return signals

    @staticmethod
    def rsi_signal(close: list[float], window: int = 14, overbought: float = 70.0, oversold: float = 30.0) -> list[int]:
        signals = []
        for i in range(len(close)):
            if i < window:
                signals.append(0)
                continue
            window_prices = close[i-window:i]
            gains = []
            losses = []
            for j in range(1, len(window_prices)):
                diff = window_prices[j] - window_prices[j-1]
                gains.append(max(diff, 0))
                losses.append(max(-diff, 0))
            avg_gain = sum(gains) / len(gains)
            avg_loss = sum(losses) / len(losses)
            if avg_loss == 0:
                signals.append(1)
            else:
                rs = avg_gain / avg_loss
                rsi = 100 - 100 / (1 + rs)
                if rsi < oversold:
                    signals.append(1)
                elif rsi > overbought:
                    signals.append(-1)
                else:
                    signals.append(0)
        return signals

    @staticmethod
    def bollinger_signal(close: list[float], window: int = 20, num_std: float = 2.0) -> list[int]:
        signals = []
        for i in range(len(close)):
            if i < window - 1:
                signals.append(0)
                continue
            window_prices = close[i-window+1:i+1]
            mean = sum(window_prices) / window
            var = sum((p - mean) ** 2 for p in window_prices) / window
            std = math.sqrt(var)
            upper = mean + num_std * std
            lower = mean - num_std * std
            if close[i] < lower:
                signals.append(1)
            elif close[i] > upper:
                signals.append(-1)
            else:
                signals.append(0)
        return signals

    @staticmethod
    def macd_signal(close: list[float], fast: int = 12, slow: int = 26, signal: int = 9) -> list[int]:
        def ema(prices, period):
            k = 2 / (period + 1)
            result = [prices[0]]
            for i in range(1, len(prices)):
                result.append(prices[i] * k + result[-1] * (1 - k))
            return result
        ema_fast = ema(close, fast)
        ema_slow = ema(close, slow)
        macd_line = [f - s for f, s in zip(ema_fast, ema_slow)]
        signal_line = ema(macd_line, signal)
        signals = []
        for i in range(len(close)):
            if macd_line[i] > signal_line[i]:
                signals.append(1)
            elif macd_line[i] < signal_line[i]:
                signals.append(-1)
            else:
                signals.append(0)
        return signals


@dataclass
class PositionSizer:
    """Vectorized position sizing."""
    @staticmethod
    def fixed_fraction(capital: float, price: float, fraction: float = 0.1) -> float:
        return (capital * fraction) / price if price > 0 else 0.0

    @staticmethod
    def fixed_quantity(capital: float, price: float, quantity: float) -> float:
        return quantity

    @staticmethod
    def kelly(win_prob: float, win_loss_ratio: float, capital: float, price: float, fraction: float = 0.25) -> float:
        if win_prob <= 0 or win_loss_ratio <= 0:
            return 0.0
        kelly_pct = (win_prob * (win_loss_ratio + 1) - 1) / win_loss_ratio
        if kelly_pct <= 0:
            return 0.0
        adjusted = kelly_pct * fraction
        return (capital * adjusted) / price if price > 0 else 0.0

    @staticmethod
    def risk_based(capital: float, price: float, stop_distance: float, risk_pct: float = 0.01) -> float:
        risk_amount = capital * risk_pct
        if stop_distance <= 0:
            return 0.0
        return risk_amount / stop_distance

    @staticmethod
    def vectorized_sizes(prices: list[float], capital: float, fraction: float = 0.1) -> list[float]:
        return [PositionSizer.fixed_fraction(capital, p, fraction) for p in prices]


@dataclass
class PortfolioSimulation:
    """Vectorized portfolio simulation."""
    @staticmethod
    def simulate(prices: list[float], signals: list[int], initial_capital: float = 10000.0) -> dict[str, Any]:
        cash = initial_capital
        position = 0.0
        portfolio_values = []
        trades = []
        entry_price = 0.0
        for i in range(len(prices)):
            if signals[i] == 1 and position == 0:
                shares = PositionSizer.fixed_fraction(cash, prices[i], 0.1)
                position = shares
                cash -= shares * prices[i]
                entry_price = prices[i]
                trades.append({"type": "BUY", "price": prices[i], "shares": shares, "index": i})
            elif signals[i] == -1 and position > 0:
                cash += position * prices[i]
                trades.append({"type": "SELL", "price": prices[i], "shares": position, "index": i})
                pnl = (prices[i] - entry_price) / entry_price
                trades[-1]["pnl"] = pnl
                position = 0.0
            value = cash + position * prices[i]
            portfolio_values.append(value)
        if position > 0:
            cash += position * prices[-1]
            trades.append({"type": "SELL", "price": prices[-1], "shares": position, "index": len(prices)-1,
                          "pnl": (prices[-1] - entry_price) / entry_price})
            portfolio_values[-1] = cash
        return {"portfolio_values": portfolio_values, "trades": trades, "final_value": cash}

    @staticmethod
    def simulate_multi(price_data: dict[str, list[float]], signals: dict[str, list[int]],
                       initial_capital: float = 10000.0) -> dict[str, Any]:
        results = {}
        total_value = initial_capital
        for symbol in price_data:
            prices = price_data[symbol]
            sig = signals.get(symbol, [0] * len(prices))
            result = PortfolioSimulation.simulate(prices, sig, initial_capital * 0.2)
            results[symbol] = result
            total_value += result["final_value"] - initial_capital * 0.2
        returns = {}
        for symbol, result in results.items():
            initial = initial_capital * 0.2
            returns[symbol] = (result["final_value"] - initial) / initial if initial > 0 else 0.0
        return {
            "results": results,
            "returns": returns,
            "total_value": total_value,
            "total_return": (total_value - initial_capital) / initial_capital,
        }

    @staticmethod
    def calculate_returns(portfolio_values: list[float]) -> list[float]:
        returns = []
        for i in range(1, len(portfolio_values)):
            if portfolio_values[i-1] > 0:
                returns.append(portfolio_values[i] / portfolio_values[i-1] - 1)
            else:
                returns.append(0.0)
        return returns


@dataclass
class VectorbtMetrics:
    """Vectorized performance metrics."""
    @staticmethod
    def total_return(portfolio_values: list[float]) -> float:
        if not portfolio_values or portfolio_values[0] <= 0:
            return 0.0
        return (portfolio_values[-1] - portfolio_values[0]) / portfolio_values[0]

    @staticmethod
    def annualized_return(portfolio_values: list[float], periods_per_year: int = 252) -> float:
        if len(portfolio_values) < 2 or portfolio_values[0] <= 0:
            return 0.0
        total_return = VectorbtMetrics.total_return(portfolio_values)
        years = len(portfolio_values) / periods_per_year
        if years <= 0:
            return 0.0
        return (1 + total_return) ** (1 / years) - 1

    @staticmethod
    def volatility(returns: list[float], periods_per_year: int = 252) -> float:
        if len(returns) < 2:
            return 0.0
        mean = sum(returns) / len(returns)
        var = sum((r - mean) ** 2 for r in returns) / (len(returns) - 1)
        return math.sqrt(var) * math.sqrt(periods_per_year)

    @staticmethod
    def sharpe_ratio(portfolio_values: list[float], risk_free: float = 0.0, periods_per_year: int = 252) -> float:
        returns = PortfolioSimulation.calculate_returns(portfolio_values)
        if len(returns) < 2:
            return 0.0
        excess = [r - risk_free / periods_per_year for r in returns]
        mean = sum(excess) / len(excess)
        var = sum((r - mean) ** 2 for r in excess) / (len(excess) - 1)
        std = math.sqrt(var)
        if std == 0:
            return 0.0
        return (mean / std) * math.sqrt(periods_per_year)

    @staticmethod
    def sortino_ratio(portfolio_values: list[float], risk_free: float = 0.0, periods_per_year: int = 252) -> float:
        returns = PortfolioSimulation.calculate_returns(portfolio_values)
        if len(returns) < 2:
            return 0.0
        excess = [r - risk_free / periods_per_year for r in returns]
        mean = sum(excess) / len(excess)
        downside = [min(0, r) for r in excess]
        downside_var = sum(d ** 2 for d in downside) / len(downside)
        downside_std = math.sqrt(downside_var)
        if downside_std == 0:
            return 0.0
        return (mean / downside_std) * math.sqrt(periods_per_year)

    @staticmethod
    def max_drawdown(portfolio_values: list[float]) -> float:
        if not portfolio_values:
            return 0.0
        peak = portfolio_values[0]
        max_dd = 0.0
        for v in portfolio_values:
            if v > peak:
                peak = v
            dd = (peak - v) / peak if peak > 0 else 0.0
            if dd > max_dd:
                max_dd = dd
        return max_dd

    @staticmethod
    def calmar_ratio(portfolio_values: list[float], periods_per_year: int = 252) -> float:
        ann_return = VectorbtMetrics.annualized_return(portfolio_values, periods_per_year)
        max_dd = VectorbtMetrics.max_drawdown(portfolio_values)
        if max_dd <= 0:
            return 0.0
        return ann_return / max_dd

    @staticmethod
    def calculate_all(portfolio_values: list[float]) -> dict[str, float]:
        returns = PortfolioSimulation.calculate_returns(portfolio_values)
        return {
            "total_return": VectorbtMetrics.total_return(portfolio_values),
            "annualized_return": VectorbtMetrics.annualized_return(portfolio_values),
            "volatility": VectorbtMetrics.volatility(returns),
            "sharpe_ratio": VectorbtMetrics.sharpe_ratio(portfolio_values),
            "sortino_ratio": VectorbtMetrics.sortino_ratio(portfolio_values),
            "max_drawdown": VectorbtMetrics.max_drawdown(portfolio_values),
            "calmar_ratio": VectorbtMetrics.calmar_ratio(portfolio_values),
        }


@dataclass
class VectorbtPortfolio:
    """Unified vectorbt portfolio engine."""
    @staticmethod
    def from_signals(prices: list[float], signals: list[int], initial_capital: float = 10000.0) -> dict[str, Any]:
        sim = PortfolioSimulation.simulate(prices, signals, initial_capital)
        metrics = VectorbtMetrics.calculate_all(sim["portfolio_values"])
        return {"simulation": sim, "metrics": metrics}

    @staticmethod
    def compare_strategies(prices: list[float], strategies: dict[str, list[int]],
                           initial_capital: float = 10000.0) -> dict[str, Any]:
        results = {}
        for name, signals in strategies.items():
            sim = PortfolioSimulation.simulate(prices, signals, initial_capital)
            metrics = VectorbtMetrics.calculate_all(sim["portfolio_values"])
            results[name] = {"metrics": metrics, "final_value": sim["final_value"]}
        best_strategy = max(results, key=lambda k: results[k]["metrics"]["sharpe_ratio"])
        return {"strategies": results, "best_strategy": best_strategy}

    @staticmethod
    def rolling_sharpe(portfolio_values: list[float], window: int = 20) -> list[float]:
        returns = PortfolioSimulation.calculate_returns(portfolio_values)
        sharpe_values = []
        for i in range(len(returns)):
            if i < window - 1:
                sharpe_values.append(float('nan'))
                continue
            window_returns = returns[i-window+1:i+1]
            mean = sum(window_returns) / len(window_returns)
            var = sum((r - mean) ** 2 for r in window_returns) / (len(window_returns) - 1)
            std = math.sqrt(var)
            sharpe = (mean / std) * math.sqrt(252) if std > 0 else 0.0
            sharpe_values.append(sharpe)
        return sharpe_values

    @staticmethod
    def generate_random_signals(prices: list[float], strategy: str = "random") -> list[int]:
        signals = []
        if strategy == "random":
            for _ in range(len(prices)):
                signals.append(random.choice([-1, 0, 1]))
        elif strategy == "momentum":
            for i in range(len(prices)):
                if i >= 5:
                    if prices[i] > prices[i-5]:
                        signals.append(1)
                    elif prices[i] < prices[i-5]:
                        signals.append(-1)
                    else:
                        signals.append(0)
                else:
                    signals.append(0)
        return signals
