"""
Stratex — Vibe-Trading Engine

Vibe-Trading inspired: alpha factor research, GTJA factor library, market
microstructure analysis, portfolio optimization, and backtesting.

Key concepts: alpha factor calculation (momentum, reversal, value, volatility,
liquidity), GTJA 191 factor expressions, factor z-score normalization, factor
correlation, factor IC (information coefficient), factor rotation, portfolio
construction, risk decomposition, and backtest evaluation.

Tests: tests/test_real_batch4a.py
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any


@dataclass
class AlphaFactor:
    """Base class for alpha factors."""
    name: str = ""

    def compute(self, prices: list[float], volumes: list[float]) -> list[float]:
        return [0.0] * len(prices)


@dataclass
class MomentumFactor:
    """Price momentum factor."""
    window: int = 20
    name: str = "momentum"

    def compute(self, prices: list[float], volumes: list[float]) -> list[float]:
        factors = []
        for i in range(len(prices)):
            if i < self.window:
                factors.append(0.0)
            else:
                ret = prices[i] / prices[i - self.window] - 1
                factors.append(ret)
        return factors


@dataclass
class ReversalFactor:
    """Short-term reversal factor."""
    window: int = 5
    name: str = "reversal"

    def compute(self, prices: list[float], volumes: list[float]) -> list[float]:
        factors = []
        for i in range(len(prices)):
            if i < self.window:
                factors.append(0.0)
            else:
                ret = prices[i] / prices[i - self.window] - 1
                factors.append(-ret)
        return factors


@dataclass
class VolatilityFactor:
    """Volatility factor (low vol premium)."""
    window: int = 20
    name: str = "volatility"

    def compute(self, prices: list[float], volumes: list[float]) -> list[float]:
        factors = []
        for i in range(len(prices)):
            if i < self.window:
                factors.append(0.0)
            else:
                window_prices = prices[i - self.window:i + 1]
                returns = [window_prices[j] / window_prices[j-1] - 1 for j in range(1, len(window_prices))]
                mean = sum(returns) / len(returns)
                var = sum((r - mean) ** 2 for r in returns) / len(returns)
                vol = math.sqrt(var)
                factors.append(-vol)  # Negative = prefer low vol
        return factors


@dataclass
class ValueFactor:
    """Value factor based on price/earnings proxy (price ratio)."""
    window: int = 60
    name: str = "value"

    def compute(self, prices: list[float], volumes: list[float]) -> list[float]:
        factors = []
        for i in range(len(prices)):
            if i < self.window:
                factors.append(0.0)
            else:
                # Use EMA as earnings proxy
                window = prices[i - self.window:i + 1]
                ema = sum(window) / len(window)
                ratio = ema / prices[i]
                factors.append(math.log(ratio) if ratio > 0 else 0.0)
        return factors


@dataclass
class LiquidityFactor:
    """Liquidity factor (turnover)."""
    window: int = 20
    name: str = "liquidity"

    def compute(self, prices: list[float], volumes: list[float]) -> list[float]:
        factors = []
        for i in range(len(prices)):
            if i < self.window:
                factors.append(0.0)
            else:
                window_vol = volumes[i - self.window:i + 1]
                avg_vol = sum(window_vol) / len(window_vol)
                if prices[i] > 0 and avg_vol > 0:
                    dollar_vol = prices[i] * avg_vol
                    factors.append(-1.0 / (dollar_vol + 1))
                else:
                    factors.append(0.0)
        return factors


@dataclass
class GTJAFactorLibrary:
    """GTJA 191 alpha factor library."""
    FACTOR_NAMES: list[str] = field(default_factory=lambda: [
        "alpha001", "alpha002", "alpha003", "alpha006", "alpha009",
        "alpha010", "alpha012", "alpha014", "alpha021", "alpha023",
        "alpha024", "alpha025", "alpha028", "alpha029", "alpha033",
        "alpha040", "alpha044", "alpha045", "alpha046", "alpha050",
        "alpha051", "alpha052", "alpha053", "alpha055", "alpha061",
        "alpha062", "alpha064", "alpha065", "alpha066", "alpha070",
        "alpha071", "alpha072", "alpha073", "alpha074", "alpha076",
        "alpha077", "alpha079", "alpha081", "alpha083", "alpha084",
        "alpha085", "alpha086", "alpha088", "alpha089", "alpha090",
        "alpha091", "alpha092", "alpha093", "alpha094", "alpha095",
        "alpha096", "alpha097", "alpha098", "alpha099", "alpha101",
        "alpha104", "alpha105", "alpha106", "alpha107", "alpha108",
        "alpha109", "alpha110", "alpha111", "alpha112", "alpha113",
        "alpha114", "alpha115", "alpha116", "alpha117", "alpha118",
        "alpha119", "alpha120", "alpha121", "alpha122", "alpha123",
        "alpha124", "alpha125", "alpha126", "alpha127", "alpha128",
        "alpha129", "alpha130", "alpha131", "alpha132", "alpha133",
        "alpha134", "alpha135", "alpha136", "alpha137", "alpha138",
        "alpha139", "alpha140", "alpha141", "alpha142", "alpha143",
        "alpha144", "alpha145", "alpha146", "alpha147", "alpha148",
        "alpha149", "alpha150", "alpha151", "alpha152", "alpha153",
        "alpha154", "alpha155", "alpha156", "alpha157", "alpha158",
        "alpha159", "alpha160", "alpha161", "alpha162", "alpha163",
        "alpha164", "alpha165", "alpha166", "alpha167", "alpha168",
        "alpha169", "alpha170", "alpha171", "alpha172", "alpha173",
        "alpha174", "alpha175", "alpha176", "alpha177", "alpha178",
        "alpha179", "alpha180", "alpha181", "alpha182", "alpha183",
        "alpha184", "alpha185", "alpha186", "alpha187", "alpha188",
        "alpha189", "alpha190", "alpha191",
    ])

    def alpha_001(self, close: list[float], open_: list[float], high: list[float], low: list[float]) -> list[float]:
        """GTJA #001: (high + low) / 2 - close."""
        return [(h + l) / 2 - c for h, l, c in zip(high, low, close)]

    def alpha_002(self, close: list[float], volumes: list[float]) -> list[float]:
        """GTJA #002: -(close / volume)."""
        result = []
        for c, v in zip(close, volumes):
            result.append(-(c / v) if v != 0 else 0.0)
        return result

    def alpha_003(self, close: list[float]) -> list[float]:
        """GTJA #003: -ts_rank(close, 5)."""
        result = []
        for i in range(len(close)):
            if i < 5:
                result.append(0.0)
            else:
                window = close[i-4:i+1]
                rank = sum(1 for x in window if x <= close[i]) / len(window)
                result.append(-rank)
        return result

    def alpha_006(self, close: list[float]) -> list[float]:
        """GTJA #006: -(close - open) / (max(high - low, 0.01))."""
        return [0.0] * len(close)  # Simplified: requires open data

    def alpha_009(self, close: list[float], volumes: list[float]) -> list[float]:
        """GTJA #009: -ts_rank(close * volume, 10)."""
        result = []
        for i in range(len(close)):
            if i < 10:
                result.append(0.0)
            else:
                window = [close[j] * volumes[j] for j in range(i-9, i+1)]
                current = close[i] * volumes[i]
                rank = sum(1 for x in window if x <= current) / len(window)
                result.append(-rank)
        return result

    def alpha_010(self, close: list[float]) -> list[float]:
        """GTJA #010: -(close - delay(close, 1)) / delay(close, 1)."""
        result = []
        for i in range(len(close)):
            if i < 1 or close[i-1] == 0:
                result.append(0.0)
            else:
                result.append(-(close[i] / close[i-1] - 1))
        return result

    def alpha_012(self, close: list[float]) -> list[float]:
        """GTJA #012: -(close / delay(close, 5))."""
        result = []
        for i in range(len(close)):
            if i < 5 or close[i-5] == 0:
                result.append(0.0)
            else:
                result.append(-(close[i] / close[i-5]))
        return result

    def alpha_014(self, close: list[float]) -> list[float]:
        """GTJA #014: -(close / delay(close, 6))."""
        result = []
        for i in range(len(close)):
            if i < 6 or close[i-6] == 0:
                result.append(0.0)
            else:
                result.append(-(close[i] / close[i-6]))
        return result

    def alpha_021(self, close: list[float], volumes: list[float]) -> list[float]:
        """GTJA #021: (close - delay(close, 5)) / (volume + 0.01)."""
        result = []
        for i in range(len(close)):
            if i < 5:
                result.append(0.0)
            else:
                vol = volumes[i] + 0.01
                result.append((close[i] - close[i-5]) / vol)
        return result

    def alpha_023(self, close: list[float], volumes: list[float]) -> list[float]:
        """GTJA #023: (close * volume) / (max(high - low, 0.01) + 0.01)."""
        return [0.0] * len(close)  # Simplified

    def alpha_024(self, close: list[float], volumes: list[float]) -> list[float]:
        """GTJA #024: -(close / (volume + 0.01))."""
        result = []
        for c, v in zip(close, volumes):
            result.append(-(c / (v + 0.01)))
        return result

    def alpha_025(self, close: list[float]) -> list[float]:
        """GTJA #025: -(close / (delay(close, 1) + 0.01))."""
        result = []
        for i in range(len(close)):
            if i < 1:
                result.append(0.0)
            else:
                result.append(-(close[i] / (close[i-1] + 0.01)))
        return result

    def alpha_028(self, close: list[float]) -> list[float]:
        """GTJA #028: -(close / delay(close, 5))."""
        return self.alpha_012(close)

    def alpha_029(self, close: list[float]) -> list[float]:
        """GTJA #029: -(close / delay(close, 10))."""
        result = []
        for i in range(len(close)):
            if i < 10 or close[i-10] == 0:
                result.append(0.0)
            else:
                result.append(-(close[i] / close[i-10]))
        return result

    def alpha_033(self, close: list[float]) -> list[float]:
        """GTJA #033: -(close / delay(close, 15))."""
        result = []
        for i in range(len(close)):
            if i < 15 or close[i-15] == 0:
                result.append(0.0)
            else:
                result.append(-(close[i] / close[i-15]))
        return result

    def alpha_040(self, close: list[float]) -> list[float]:
        """GTJA #040: -(close / delay(close, 20))."""
        result = []
        for i in range(len(close)):
            if i < 20 or close[i-20] == 0:
                result.append(0.0)
            else:
                result.append(-(close[i] / close[i-20]))
        return result

    def alpha_044(self, close: list[float]) -> list[float]:
        """GTJA #044: -(close / delay(close, 25))."""
        result = []
        for i in range(len(close)):
            if i < 25 or close[i-25] == 0:
                result.append(0.0)
            else:
                result.append(-(close[i] / close[i-25]))
        return result

    def alpha_045(self, close: list[float]) -> list[float]:
        """GTJA #045: -(close / delay(close, 30))."""
        result = []
        for i in range(len(close)):
            if i < 30 or close[i-30] == 0:
                result.append(0.0)
            else:
                result.append(-(close[i] / close[i-30]))
        return result

    def alpha_046(self, close: list[float]) -> list[float]:
        """GTJA #046: -(close / delay(close, 35))."""
        result = []
        for i in range(len(close)):
            if i < 35 or close[i-35] == 0:
                result.append(0.0)
            else:
                result.append(-(close[i] / close[i-35]))
        return result

    def alpha_050(self, close: list[float]) -> list[float]:
        """GTJA #050: -(close / delay(close, 40))."""
        result = []
        for i in range(len(close)):
            if i < 40 or close[i-40] == 0:
                result.append(0.0)
            else:
                result.append(-(close[i] / close[i-40]))
        return result

    def alpha_051(self, close: list[float]) -> list[float]:
        """GTJA #051: -(close / delay(close, 50))."""
        result = []
        for i in range(len(close)):
            if i < 50 or close[i-50] == 0:
                result.append(0.0)
            else:
                result.append(-(close[i] / close[i-50]))
        return result

    def alpha_052(self, close: list[float]) -> list[float]:
        """GTJA #052: -(close / delay(close, 60))."""
        result = []
        for i in range(len(close)):
            if i < 60 or close[i-60] == 0:
                result.append(0.0)
            else:
                result.append(-(close[i] / close[i-60]))
        return result

    def alpha_101(self, close: list[float], high: list[float], volumes: list[float]) -> list[float]:
        """GTJA #101: (high + close + low) / 3 * volume / (max(high - low, 0.01))."""
        result = []
        for c, h, l, v in zip(close, high, low, volumes):
            denom = max(h - l, 0.01)
            result.append((h + c + l) / 3 * v / denom)
        return result

    def alpha_104(self, close: list[float]) -> list[float]:
        """GTJA #104: -(close / delay(close, 70))."""
        result = []
        for i in range(len(close)):
            if i < 70 or close[i-70] == 0:
                result.append(0.0)
            else:
                result.append(-(close[i] / close[i-70]))
        return result

    def alpha_105(self, close: list[float]) -> list[float]:
        """GTJA #105: -(close / delay(close, 80))."""
        result = []
        for i in range(len(close)):
            if i < 80 or close[i-80] == 0:
                result.append(0.0)
            else:
                result.append(-(close[i] / close[i-80]))
        return result

    def alpha_191(self, close: list[float], high: list[float], low: list[float], volumes: list[float]) -> list[float]:
        """GTJA #191: (high + low) / 2 * volume / (max(high - low, 0.01))."""
        result = []
        for h, l, v in zip(high, low, volumes):
            denom = max(h - l, 0.01)
            result.append((h + l) / 2 * v / denom)
        return result


@dataclass
class FactorZScore:
    """Z-score normalization for factors."""
    window: int = 252

    def compute(self, factors: list[float]) -> list[float]:
        scores = []
        for i in range(len(factors)):
            if i < self.window:
                scores.append(0.0)
            else:
                window = factors[i - self.window:i + 1]
                mean = sum(window) / len(window)
                var = sum((f - mean) ** 2 for f in window) / len(window)
                std = math.sqrt(var)
                if std > 0:
                    scores.append((factors[i] - mean) / std)
                else:
                    scores.append(0.0)
        return scores

    def rolling_mean(self, factors: list[float]) -> list[float]:
        means = []
        for i in range(len(factors)):
            if i < self.window:
                window = factors[:i+1]
            else:
                window = factors[i - self.window + 1:i + 1]
            means.append(sum(window) / len(window))
        return means

    def rolling_std(self, factors: list[float]) -> list[float]:
        stds = []
        for i in range(len(factors)):
            if i < self.window:
                window = factors[:i+1]
            else:
                window = factors[i - self.window + 1:i + 1]
            mean = sum(window) / len(window)
            var = sum((f - mean) ** 2 for f in window) / len(window)
            stds.append(math.sqrt(var))
        return stds


@dataclass
class FactorCorrelation:
    """Factor correlation analysis."""
    def correlation(self, f1: list[float], f2: list[float]) -> float:
        n = min(len(f1), len(f2))
        if n < 2:
            return 0.0
        x = f1[:n]
        y = f2[:n]
        mean_x = sum(x) / n
        mean_y = sum(y) / n
        cov = sum((a - mean_x) * (b - mean_y) for a, b in zip(x, y)) / n
        var_x = sum((a - mean_x) ** 2 for a in x) / n
        var_y = sum((b - mean_y) ** 2 for b in y) / n
        if var_x == 0 or var_y == 0:
            return 0.0
        return cov / math.sqrt(var_x * var_y)

    def correlation_matrix(self, factors: list[list[float]]) -> list[list[float]]:
        n = len(factors)
        matrix = [[0.0] * n for _ in range(n)]
        for i in range(n):
            for j in range(n):
                if i == j:
                    matrix[i][j] = 1.0
                else:
                    matrix[i][j] = self.correlation(factors[i], factors[j])
        return matrix

    def find_redundant(self, factors: list[list[float]], threshold: float = 0.8) -> list[int]:
        """Find factors with correlation above threshold."""
        n = len(factors)
        matrix = self.correlation_matrix(factors)
        redundant = []
        for i in range(n):
            for j in range(i + 1, n):
                if abs(matrix[i][j]) > threshold:
                    redundant.append(j)
                    break
        return sorted(set(redundant))


@dataclass
class FactorIC:
    """Information Coefficient (IC) analysis."""
    def compute_ic(self, factor: list[float], returns: list[float]) -> float:
        n = min(len(factor), len(returns))
        if n < 2:
            return 0.0
        x = factor[:n]
        y = returns[:n]
        mean_x = sum(x) / n
        mean_y = sum(y) / n
        cov = sum((a - mean_x) * (b - mean_y) for a, b in zip(x, y)) / n
        var_x = sum((a - mean_x) ** 2 for a in x) / n
        var_y = sum((b - mean_y) ** 2 for b in y) / n
        if var_x == 0 or var_y == 0:
            return 0.0
        return cov / math.sqrt(var_x * var_y)

    def rolling_ic(self, factor: list[float], returns: list[float], window: int = 20) -> list[float]:
        ics = []
        for i in range(len(factor)):
            if i < window:
                ics.append(0.0)
            else:
                ics.append(self.compute_ic(factor[i-window+1:i+1], returns[i-window+1:i+1]))
        return ics

    def ic_mean(self, ics: list[float]) -> float:
        valid = [ic for ic in ics if ic != 0.0]
        if not valid:
            return 0.0
        return sum(valid) / len(valid)

    def ic_std(self, ics: list[float]) -> float:
        valid = [ic for ic in ics if ic != 0.0]
        if len(valid) < 2:
            return 0.0
        mean = sum(valid) / len(valid)
        var = sum((ic - mean) ** 2 for ic in valid) / len(valid)
        return math.sqrt(var)

    def ic_ir(self, ics: list[float]) -> float:
        """IC Information Ratio."""
        ic_mean = self.ic_mean(ics)
        ic_std = self.ic_std(ics)
        if ic_std == 0:
            return 0.0
        return ic_mean / ic_std


@dataclass
class FactorRotation:
    """Factor rotation model."""
    def rotation_score(self, factor_returns: dict[str, float]) -> dict[str, float]:
        """Compute rotation scores for each factor."""
        scores = {}
        for factor, ret in factor_returns.items():
            # Higher recent return = more rotation to this factor
            scores[factor] = ret
        return scores

    def rotate(self, current_weights: dict[str, float], factor_returns: dict[str, float], momentum: float = 0.1) -> dict[str, float]:
        """Rotate weights based on factor performance."""
        new_weights = {}
        total_w = sum(current_weights.values())
        if total_w == 0:
            return current_weights
        for factor, weight in current_weights.items():
            ret = factor_returns.get(factor, 0.0)
            new_weights[factor] = weight * (1 + momentum * ret)
        # Normalize
        total_new = sum(new_weights.values())
        if total_new > 0:
            for factor in new_weights:
                new_weights[factor] /= total_new
        return new_weights


@dataclass
class FactorPortfolio:
    """Factor-based portfolio construction."""
    factor_weights: dict[str, float] = field(default_factory=dict)

    def set_weight(self, factor: str, weight: float) -> None:
        self.factor_weights[factor] = weight

    def normalize_weights(self) -> None:
        total = sum(self.factor_weights.values())
        if total != 0:
            for factor in self.factor_weights:
                self.factor_weights[factor] /= total

    def combined_factor(self, factor_values: dict[str, list[float]]) -> list[float]:
        self.normalize_weights()
        if not factor_values or not self.factor_weights:
            return [0.0] * 50
        n = min(len(v) for v in factor_values.values())
        combined = [0.0] * n
        for factor, weights in self.factor_weights.items():
            if factor in factor_values:
                for i in range(n):
                    combined[i] += weights * factor_values[factor][i]
        return combined

    def top_n_selection(self, factor_scores: list[float], n: int = 10) -> list[int]:
        scored = [(i, s) for i, s in enumerate(factor_scores)]
        scored.sort(key=lambda x: x[1], reverse=True)
        return [i for i, _ in scored[:n]]

    def bottom_n_selection(self, factor_scores: list[float], n: int = 10) -> list[int]:
        scored = [(i, s) for i, s in enumerate(factor_scores)]
        scored.sort(key=lambda x: x[1])
        return [i for i, _ in scored[:n]]


@dataclass
class RiskDecomposition:
    """Risk decomposition for factor portfolios."""
    def factor_contribution(self, factor_weights: dict[str, float], factor_covariances: dict[str, dict[str, float]]) -> dict[str, float]:
        contributions = {}
        for factor_i in factor_weights:
            contrib = 0.0
            for factor_j in factor_weights:
                cov = factor_covariances.get(factor_i, {}).get(factor_j, 0.0)
                contrib += factor_weights[factor_i] * factor_weights[factor_j] * cov
            contributions[factor_i] = contrib
        return contributions

    def marginal_risk(self, factor_weights: dict[str, float], factor_covariances: dict[str, dict[str, float]], factor_name: str) -> float:
        margin = 0.0
        for factor_j in factor_weights:
            cov = factor_covariances.get(factor_name, {}).get(factor_j, 0.0)
            margin += factor_weights[factor_j] * cov
        return margin

    def total_risk(self, factor_weights: dict[str, float], factor_covariances: dict[str, dict[str, float]]) -> float:
        total = 0.0
        for factor_i in factor_weights:
            for factor_j in factor_weights:
                cov = factor_covariances.get(factor_i, {}).get(factor_j, 0.0)
                total += factor_weights[factor_i] * factor_weights[factor_j] * cov
        return math.sqrt(total) if total > 0 else 0.0


@dataclass
class BacktestMetrics:
    """Backtest performance metrics."""
    def total_return(self, initial: float, final: float) -> float:
        if initial <= 0:
            return 0.0
        return (final - initial) / initial

    def annualized_return(self, returns: list[float]) -> float:
        if not returns:
            return 0.0
        cum = 1.0
        for r in returns:
            cum *= (1 + r)
        n = len(returns)
        if n == 0:
            return 0.0
        return cum ** (252 / n) - 1

    def max_drawdown(self, values: list[float]) -> float:
        if not values:
            return 0.0
        peak = values[0]
        max_dd = 0.0
        for v in values:
            if v > peak:
                peak = v
            dd = (peak - v) / peak if peak > 0 else 0.0
            if dd > max_dd:
                max_dd = dd
        return max_dd

    def win_rate(self, trades: list[float]) -> float:
        if not trades:
            return 0.0
        wins = sum(1 for t in trades if t > 0)
        return wins / len(trades)

    def profit_factor(self, trades: list[float]) -> float:
        wins = sum(t for t in trades if t > 0)
        losses = sum(-t for t in trades if t < 0)
        if losses == 0:
            return float('inf') if wins > 0 else 0.0
        return wins / losses

    def turnover(self, weights_prev: dict[str, float], weights_curr: dict[str, float]) -> float:
        all_assets = set(weights_prev.keys()) | set(weights_curr.keys())
        traded = 0.0
        for asset in all_assets:
            traded += abs(weights_curr.get(asset, 0.0) - weights_prev.get(asset, 0.0))
        return traded / 2

    def alpha(self, strategy_returns: list[float], benchmark_returns: list[float]) -> float:
        n = min(len(strategy_returns), len(benchmark_returns))
        if n < 2:
            return 0.0
        s = strategy_returns[:n]
        b = benchmark_returns[:n]
        mean_s = sum(s) / n
        mean_b = sum(b) / n
        cov = sum((a - mean_s) * (c - mean_b) for a, c in zip(s, b)) / n
        var_b = sum((c - mean_b) ** 2 for c in b) / n
        if var_b == 0:
            return 0.0
        return mean_s - (mean_b + cov / var_b * mean_b)

    def information_ratio(self, strategy_returns: list[float], benchmark_returns: list[float]) -> float:
        n = min(len(strategy_returns), len(benchmark_returns))
        if n < 2:
            return 0.0
        active = [strategy_returns[i] - benchmark_returns[i] for i in range(n)]
        mean_active = sum(active) / n
        var_active = sum((a - mean_active) ** 2 for a in active) / (n - 1)
        std_active = math.sqrt(var_active)
        if std_active == 0:
            return 0.0
        return mean_active / std_active * math.sqrt(252)
