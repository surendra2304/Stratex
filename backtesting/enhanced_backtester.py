"""
Stratex/backtesting/enhanced_backtester.py — Enhanced Backtesting Engine.

Implements:
1. Event-Driven Backtesting: simulate trades with realistic market data
2. Walk-Forward Optimization: optimize parameters on rolling windows
3. Monte Carlo Simulation: generate many randomized trading paths
4. Performance Metrics: comprehensive return, risk, and Sharpe metrics
5. Parameter Optimization: grid search for optimal strategy parameters
6. Overfitting Detection: detect and penalize overfitted strategies
"""
from __future__ import annotations

import math
import random
from dataclasses import dataclass, field
from typing import Any


@dataclass
class BacktestTrade:
    """A single trade in a backtest."""
    symbol: str
    entry_date: str
    entry_price: float
    exit_date: str
    exit_price: float
    size: float
    direction: str  # long, short
    pnl: float
    pnl_pct: float
    holding_period: float


@dataclass
class BacktestResult:
    """Complete backtest result."""
    strategy_name: str
    start_date: str
    end_date: str
    initial_capital: float
    final_capital: float
    total_return: float
    annualized_return: float
    sharpe_ratio: float
    sortino_ratio: float
    win_rate: float
    max_drawdown: float
    profit_factor: float
    total_trades: int
    avg_trade_pnl: float
    trades: list[BacktestTrade] = field(default_factory=list)


@dataclass
class OptimizationResult:
    """Parameter optimization result."""
    strategy_name: str
    best_params: dict[str, Any]
    best_sharpe: float
    best_return: float
    best_drawdown: float
    param_ranges: dict[str, list]
    results: list[dict]
    overfitting_score: float


class EnhancedBacktester:
    """Enhanced backtesting engine with optimization and simulation."""

    def __init__(self):
        self._results: dict[str, BacktestResult] = {}
        self._optimizations: dict[str, OptimizationResult] = {}

    # ── Event-Driven Backtesting ─────────────────────────────────────────────

    def run_backtest(
        self,
        strategy_name: str,
        price_data: dict[str, list[float]],
        signals: list[dict],
        initial_capital: float = 100000.0,
        fees: float = 0.001,
        slippage: float = 0.0005,
    ) -> BacktestResult:
        """Run a backtest with given signals."""
        capital = initial_capital
        positions: dict[str, dict] = {}
        trades: list[BacktestTrade] = []
        equity_curve: list[float] = [capital]

        for signal in signals:
            symbol = signal["symbol"]
            date = signal["date"]
            price = signal["price"]
            action = signal.get("action", "hold")
            size = signal.get("size", 0.01)

            if action == "buy":
                # Open long position
                cost = price * size * (1 + slippage)
                if cost < capital * 0.95:  # Don't use more than 95% of capital
                    capital -= cost
                    positions[symbol] = {
                        "entry_date": date,
                        "entry_price": price,
                        "size": size,
                        "direction": "long",
                    }

            elif action == "sell":
                # Close long or open short
                if symbol in positions and positions[symbol]["direction"] == "long":
                    # Close position
                    proceeds = price * positions[symbol]["size"] * (1 - slippage)
                    pnl = proceeds - (positions[symbol]["entry_price"] *
                                      positions[symbol]["size"] * (1 + fees))
                    capital += proceeds

                    trades.append(BacktestTrade(
                        symbol=symbol,
                        entry_date=positions[symbol]["entry_date"],
                        entry_price=positions[symbol]["entry_price"],
                        exit_date=date,
                        exit_price=price,
                        size=positions[symbol]["size"],
                        direction="long",
                        pnl=pnl,
                        pnl_pct=(price / positions[symbol]["entry_price"] - 1) * 100,
                        holding_period=0.0,  # Would need date parsing
                    ))

                    del positions[symbol]

            equity_curve.append(capital)

        # Close all remaining positions at last price
        for symbol, pos in list(positions.items()):
            if symbol in price_data:
                last_price = price_data[symbol][-1]
                proceeds = last_price * pos["size"] * (1 - slippage)
                pnl = proceeds - (pos["entry_price"] * pos["size"] * (1 + fees))
                capital += proceeds

                trades.append(BacktestTrade(
                    symbol=symbol,
                    entry_date=pos["entry_date"],
                    entry_price=pos["entry_price"],
                    exit_date="end",
                    exit_price=last_price,
                    size=pos["size"],
                    direction=pos["direction"],
                    pnl=pnl,
                    pnl_pct=(last_price / pos["entry_price"] - 1) * 100,
                    holding_period=0.0,
                ))

        # Calculate metrics
        total_return = (capital / initial_capital - 1) * 100

        # Annualized return (simplified)
        annualized = total_return * 0.167  # Assuming ~6 months of data

        # Sharpe ratio
        sharpe = self._calculate_sharpe(equity_curve)

        # Sortino ratio
        sortino = self._calculate_sortino(equity_curve)

        # Win rate
        wins = sum(1 for t in trades if t.pnl > 0)
        win_rate = wins / max(1, len(trades)) * 100

        # Max drawdown
        drawdown = self._calculate_drawdown(equity_curve)

        # Profit factor
        gross_profit = sum(t.pnl for t in trades if t.pnl > 0)
        gross_loss = sum(abs(t.pnl) for t in trades if t.pnl < 0)
        profit_factor = gross_profit / max(1.0, gross_loss)

        result = BacktestResult(
            strategy_name=strategy_name,
            start_date=signals[0]["date"] if signals else "N/A",
            end_date=signals[-1]["date"] if signals else "N/A",
            initial_capital=initial_capital,
            final_capital=capital,
            total_return=total_return,
            annualized_return=annualized,
            sharpe_ratio=sharpe,
            sortino_ratio=sortino,
            win_rate=win_rate,
            max_drawdown=drawdown,
            profit_factor=profit_factor,
            total_trades=len(trades),
            avg_trade_pnl=sum(t.pnl for t in trades) / max(1, len(trades)),
            trades=trades,
        )

        self._results[strategy_name] = result
        return result

    def _calculate_sharpe(self, equity_curve: list[float]) -> float:
        """Calculate Sharpe ratio."""
        if len(equity_curve) < 2:
            return 0.0

        returns = []
        for i in range(1, len(equity_curve)):
            returns.append(equity_curve[i] / equity_curve[i-1] - 1)

        if not returns:
            return 0.0

        mean_return = sum(returns) / len(returns)
        variance = sum((r - mean_return) ** 2 for r in returns) / len(returns)
        std = math.sqrt(variance)

        if std == 0:
            return 0.0

        return (mean_return / std) * math.sqrt(252)

    def _calculate_sortino(self, equity_curve: list[float]) -> float:
        """Calculate Sortino ratio."""
        if len(equity_curve) < 2:
            return 0.0

        returns = []
        for i in range(1, len(equity_curve)):
            returns.append(equity_curve[i] / equity_curve[i-1] - 1)

        if not returns:
            return 0.0

        mean_return = sum(returns) / len(returns)
        downside_returns = [r for r in returns if r < 0]

        if not downside_returns:
            return float("inf")

        downside_variance = sum(r ** 2 for r in downside_returns) / len(downside_returns)
        downside_std = math.sqrt(downside_variance)

        if downside_std == 0:
            return 0.0

        return (mean_return / downside_std) * math.sqrt(252)

    def _calculate_drawdown(self, equity_curve: list[float]) -> float:
        """Calculate maximum drawdown."""
        if not equity_curve:
            return 0.0

        peak = equity_curve[0]
        max_dd = 0.0

        for value in equity_curve:
            if value > peak:
                peak = value
            dd = (peak - value) / peak
            if dd > max_dd:
                max_dd = dd

        return max_dd * 100

    # ── Walk-Forward Optimization ────────────────────────────────────────────

    def walk_forward_optimize(
        self,
        strategy_name: str,
        price_data: dict[str, list[float]],
        param_ranges: dict[str, list],
        window_size: int = 50,
        step_size: int = 10,
    ) -> OptimizationResult:
        """Walk-forward optimization of strategy parameters."""
        results = []
        best_sharpe = 0.0
        best_params = {}
        best_return = 0.0
        best_drawdown = 0.0

        # Generate parameter combinations
        param_combinations = self._generate_combinations(param_ranges)

        # Walk-forward windows
        prices = price_data.get(list(price_data.keys())[0], [])
        if len(prices) < window_size + step_size:
            return OptimizationResult(
                strategy_name=strategy_name,
                best_params={},
                best_sharpe=0.0,
                best_return=0.0,
                best_drawdown=0.0,
                param_ranges=param_ranges,
                results=[],
                overfitting_score=1.0,
            )

        for start in range(0, len(prices) - window_size - step_size, step_size):
            end = start + window_size

            # Optimize on training window
            for params in param_combinations:
                # Generate signals (simplified)
                signals = self._generate_signals_simple(prices[start:end], params)

                result = self.run_backtest(
                    strategy_name=f"{strategy_name}_tf{start}",
                    price_data={list(price_data.keys())[0]: prices[start:end]},
                    signals=signals,
                )

                if result.sharpe_ratio > best_sharpe:
                    best_sharpe = result.sharpe_ratio
                    best_params = params
                    best_return = result.total_return
                    best_drawdown = result.max_drawdown

                results.append({
                    "window": (start, end),
                    "params": params,
                    "sharpe": result.sharpe_ratio,
                    "return": result.total_return,
                    "drawdown": result.max_drawdown,
                })

        # Calculate overfitting score
        overfitting = self._calculate_overfitting(results)

        optimization = OptimizationResult(
            strategy_name=strategy_name,
            best_params=best_params,
            best_sharpe=best_sharpe,
            best_return=best_return,
            best_drawdown=best_drawdown,
            param_ranges=param_ranges,
            results=results,
            overfitting_score=overfitting,
        )

        self._optimizations[strategy_name] = optimization
        return optimization

    def _generate_combinations(self, param_ranges: dict[str, list]) -> list[dict]:
        """Generate all parameter combinations."""
        if not param_ranges:
            return [{}]

        keys = list(param_ranges.keys())
        values = list(param_ranges.values())
        combinations = []

        def cartesian(idx, current):
            if idx == len(keys):
                combinations.append(current.copy())
                return
            for val in values[idx]:
                current[keys[idx]] = val
                cartesian(idx + 1, current)
                current.pop(keys[idx])

        cartesian(0, {})
        return combinations

    def _generate_signals_simple(
        self,
        prices: list[float],
        params: dict,
    ) -> list[dict]:
        """Generate simple trading signals."""
        signals = []
        sma_short = params.get("sma_short", 10)
        sma_long = params.get("sma_long", 30)

        for i in range(max(sma_long, 1), len(prices)):
            short_ma = sum(prices[i-sma_short:i]) / sma_short
            long_ma = sum(prices[i-sma_long:i]) / sma_long

            signal = {
                "symbol": "SYMBOL",
                "date": f"Day{i}",
                "price": prices[i],
                "action": "hold",
                "size": params.get("position_size", 0.01),
            }

            if short_ma > long_ma and prices[i-1] <= prices[i]:
                signal["action"] = "buy"
            elif short_ma < long_ma and prices[i-1] >= prices[i]:
                signal["action"] = "sell"

            signals.append(signal)

        return signals

    def _calculate_overfitting(self, results: list[dict]) -> float:
        """Calculate overfitting score."""
        if not results:
            return 1.0

        sharpes = [r["sharpe"] for r in results]
        mean_sharpe = sum(sharpes) / len(sharpes)
        max_sharpe = max(sharpes)

        # If best sharpe is much higher than average, high overfitting
        if mean_sharpe > 0:
            ratio = max_sharpe / mean_sharpe
            return min(1.0, ratio / 3.0)
        return 1.0

    # ── Monte Carlo Simulation ────────────────────────────────────────────────

    def monte_carlo_simulation(
        self,
        strategy_name: str,
        price_data: dict[str, list[float]],
        signals: list[dict],
        simulations: int = 1000,
        initial_capital: float = 100000.0,
    ) -> dict[str, Any]:
        """Run Monte Carlo simulation."""
        all_final_capitals = []

        for _ in range(simulations):
            # Randomize signal timing
            shuffled_signals = signals[:]
            random.shuffle(shuffled_signals)

            result = self.run_backtest(
                strategy_name=f"{strategy_name}_mc_{_}",
                price_data=price_data,
                signals=shuffled_signals,
                initial_capital=initial_capital,
            )
            all_final_capitals.append(result.final_capital)

        all_final_capitals.sort()

        # Calculate statistics
        mean_capital = sum(all_final_capitals) / len(all_final_capitals)
        p5_capital = all_final_capitals[int(len(all_final_capitals) * 0.05)]
        p50_capital = all_final_capitals[int(len(all_final_capitals) * 0.5)]
        p95_capital = all_final_capitals[int(len(all_final_capitals) * 0.95)]

        return {
            "strategy_name": strategy_name,
            "simulations": simulations,
            "mean_final_capital": mean_capital,
            "p5_final_capital": p5_capital,
            "p50_final_capital": p50_capital,
            "p95_final_capital": p95_capital,
            "probability_of_profit": (
                sum(1 for c in all_final_capitals if c > initial_capital) / len(all_final_capitals)
            ),
            "worst_case_return": (p5_capital / initial_capital - 1) * 100,
            "best_case_return": (p95_capital / initial_capital - 1) * 100,
            "expected_return": (mean_capital / initial_capital - 1) * 100,
        }

    # ── Reporting ──────────────────────────────────────────────────────────

    def get_backtest_report(self) -> dict[str, Any]:
        """Generate a backtesting report."""
        return {
            "total_backtests": len(self._results),
            "total_optimizations": len(self._optimizations),
            "strategies": list(self._results.keys()),
            "best_strategy": (
                max(self._results.values(), key=lambda r: r.sharpe_ratio).strategy_name
                if self._results else "N/A"
            ),
        }
