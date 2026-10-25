"""
Stratex — FinceptTerminal Engine

FinceptTerminal inspired: advanced market analytics, investment research,
economic data tools, and financial visualization.

Key concepts: candlestick pattern recognition, technical indicators (RSI,
MACD, Bollinger Bands, moving averages), order flow analysis, depth of
market, correlation analysis, cross-asset analysis, economic calendar,
financial statement analysis, and portfolio analytics.

Tests: tests/test_real_batch4a.py
"""

from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any


@dataclass
class CandlestickPattern:
    """Candlestick pattern recognition."""
    @staticmethod
    def bullish_engulfing(open_: list[float], close: list[float]) -> list[bool]:
        patterns = []
        for i in range(1, len(close)):
            if i < 2:
                patterns.append(False)
                continue
            prev_bull = close[i-1] > open_[i-1]
            curr_bull = close[i] > open_[i]
            body_prev = abs(close[i-1] - open_[i-1])
            body_curr = abs(close[i] - open_[i])
            if prev_bull and not curr_bull and body_curr > body_prev:
                patterns.append(True)
            else:
                patterns.append(False)
        patterns.insert(0, False)
        return patterns

    @staticmethod
    def bearish_engulfing(open_: list[float], close: list[float]) -> list[bool]:
        patterns = []
        for i in range(1, len(close)):
            if i < 2:
                patterns.append(False)
                continue
            prev_bear = close[i-1] < open_[i-1]
            curr_bear = close[i] < open_[i]
            body_prev = abs(close[i-1] - open_[i-1])
            body_curr = abs(close[i] - open_[i])
            if prev_bear and not curr_bear and body_curr > body_prev:
                patterns.append(True)
            else:
                patterns.append(False)
        patterns.insert(0, False)
        return patterns

    @staticmethod
    def hammer(low: list[float], high: list[float], close: list[float]) -> list[bool]:
        patterns = []
        for i in range(len(close)):
            body = abs(close[i] - (low[i] + high[i]) / 2)
            rng = high[i] - low[i]
            if rng > 0 and body / rng < 0.3:
                patterns.append(True)
            else:
                patterns.append(False)
        return patterns

    @staticmethod
    def doji(open_: list[float], close: list[float]) -> list[bool]:
        patterns = []
        for i in range(len(close)):
            body = abs(close[i] - open_[i])
            if body < 0.01:
                patterns.append(True)
            else:
                patterns.append(False)
        return patterns

    @staticmethod
    def pattern_score(patterns: list[bool]) -> float:
        if not patterns:
            return 0.0
        return sum(patterns) / len(patterns)


@dataclass
class TechnicalIndicators:
    """Technical analysis indicators."""
    @staticmethod
    def sma(prices: list[float], window: int) -> list[float]:
        result = []
        for i in range(len(prices)):
            if i < window - 1:
                result.append(float('nan'))
            else:
                result.append(sum(prices[i-window+1:i+1]) / window)
        return result

    @staticmethod
    def ema(prices: list[float], window: int) -> list[float]:
        if not prices:
            return []
        k = 2 / (window + 1)
        result = [prices[0]]
        for i in range(1, len(prices)):
            result.append(prices[i] * k + result[-1] * (1 - k))
        return result

    @staticmethod
    def rsi(prices: list[float], window: int = 14) -> list[float]:
        if len(prices) < 2:
            return []
        gains = []
        losses = []
        for i in range(1, len(prices)):
            diff = prices[i] - prices[i-1]
            gains.append(max(diff, 0))
            losses.append(max(-diff, 0))
        result = []
        avg_gain = sum(gains[:window]) / window
        avg_loss = sum(losses[:window]) / window
        for i in range(window, len(gains)):
            avg_gain = (avg_gain * (window - 1) + gains[i]) / window
            avg_loss = (avg_loss * (window - 1) + losses[i]) / window
            if avg_loss == 0:
                result.append(100.0)
            else:
                rs = avg_gain / avg_loss
                result.append(100 - 100 / (1 + rs))
        return result

    @staticmethod
    def macd(prices: list[float], fast: int = 12, slow: int = 26, signal: int = 9) -> tuple[list[float], list[float], list[float]]:
        ema_fast = TechnicalIndicators.ema(prices, fast)
        ema_slow = TechnicalIndicators.ema(prices, slow)
        macd_line = [f - s for f, s in zip(ema_fast, ema_slow)]
        signal_line = TechnicalIndicators.ema(macd_line, signal)
        histogram = [m - s for m, s in zip(macd_line, signal_line)]
        return macd_line, signal_line, histogram

    @staticmethod
    def bollinger_bands(prices: list[float], window: int = 20, num_std: float = 2.0) -> tuple[list[float], list[float], list[float]]:
        mid = TechnicalIndicators.sma(prices, window)
        upper = []
        lower = []
        for i in range(len(prices)):
            if i < window - 1:
                upper.append(float('nan'))
                lower.append(float('nan'))
            else:
                window_prices = prices[i-window+1:i+1]
                mean = sum(window_prices) / window
                var = sum((p - mean) ** 2 for p in window_prices) / window
                std = math.sqrt(var)
                upper.append(mean + num_std * std)
                lower.append(mean - num_std * std)
        return upper, mid, lower

    @staticmethod
    def atr(high: list[float], low: list[float], close: list[float], window: int = 14) -> list[float]:
        if len(close) < 2:
            return []
        trs = []
        for i in range(1, len(close)):
            tr = max(
                high[i] - low[i],
                abs(high[i] - close[i-1]),
                abs(low[i] - close[i-1])
            )
            trs.append(tr)
        result = []
        avg_tr = sum(trs[:window]) / window
        for i in range(window, len(trs)):
            avg_tr = (avg_tr * (window - 1) + trs[i]) / window
            result.append(avg_tr)
        return result

    @staticmethod
    def stochastic(high: list[float], low: list[float], close: list[float], window: int = 14) -> tuple[list[float], list[float]]:
        k_values = []
        d_values = []
        for i in range(len(close)):
            if i < window - 1:
                k_values.append(float('nan'))
            else:
                window_high = max(high[i-window+1:i+1])
                window_low = min(low[i-window+1:i+1])
                if window_high == window_low:
                    k = 50.0
                else:
                    k = (close[i] - window_low) / (window_high - window_low) * 100
                k_values.append(k)
        for i in range(len(k_values)):
            if i < 3 or math.isnan(k_values[i]):
                d_values.append(float('nan'))
            else:
                d_values.append(sum(k_values[i-2:i+1]) / 3)
        return k_values, d_values

    @staticmethod
    def obv(close: list[float], volumes: list[float]) -> list[float]:
        obv = [0.0]
        for i in range(1, len(close)):
            if close[i] > close[i-1]:
                obv.append(obv[-1] + volumes[i])
            elif close[i] < close[i-1]:
                obv.append(obv[-1] - volumes[i])
            else:
                obv.append(obv[-1])
        return obv

    @staticmethod
    def vwap(high: list[float], low: list[float], close: list[float], volumes: list[float]) -> list[float]:
        if not volumes or sum(volumes) == 0:
            return [0.0] * len(close)
        cum_pv = 0.0
        cum_v = 0.0
        result = []
        for h, l, c, v in zip(high, low, close, volumes):
            tp = (h + l + c) / 3
            cum_pv += tp * v
            cum_v += v
            result.append(cum_pv / cum_v if cum_v > 0 else 0.0)
        return result

    @staticmethod
    def ichimoku(high: list[float], low: list[float]) -> dict[str, list[float]]:
        period = 26
        offset = 52
        conversion = []
        baseline = []
        span_a = []
        span_b = []
        for i in range(len(high)):
            if i < period - 1:
                conversion.append(float('nan'))
                baseline.append(float('nan'))
                span_a.append(float('nan'))
                span_b.append(float('nan'))
            else:
                wh = max(high[i-period+1:i+1])
                wl = min(low[i-period+1:i+1])
                conversion.append((wh + wl) / 2)
                if i >= offset - 1:
                    baseline.append((max(high[i-offset+1:i+1]) + min(low[i-offset+1:i+1])) / 2)
                    span_a.append((conversion[-1] + baseline[-1]) / 2)
                    span_b.append((wh + wl) / 2)
                else:
                    baseline.append(float('nan'))
                    span_a.append(float('nan'))
                    span_b.append(float('nan'))
        return {
            "conversion": conversion,
            "baseline": baseline,
            "span_a": span_a,
            "span_b": span_b,
        }


@dataclass
class OrderFlowAnalysis:
    """Order flow analysis."""
    @staticmethod
    def bid_ask_imbalance(bids: list[tuple[float, float]], asks: list[tuple[float, float]]) -> float:
        bid_vol = sum(q for _, q in bids)
        ask_vol = sum(q for _, q in asks)
        total = bid_vol + ask_vol
        if total == 0:
            return 0.0
        return (bid_vol - ask_vol) / total

    @staticmethod
    def depth_profile(bids: list[tuple[float, float]], asks: list[tuple[float, float]], levels: int = 5) -> dict[str, list[tuple[float, float]]]:
        bid_levels = sorted(bids, key=lambda x: x[0], reverse=True)[:levels]
        ask_levels = sorted(asks, key=lambda x: x[0])[:levels]
        return {"bids": bid_levels, "asks": ask_levels}

    @staticmethod
    def microprice(bids: list[tuple[float, float]], asks: list[tuple[float, float]]) -> float:
        bid_vol = sum(q for _, q in bids)
        ask_vol = sum(q for _, q in asks)
        if bid_vol == 0 and ask_vol == 0:
            return 0.0
        bid_price = sum(p * q for p, q in bids) / max(bid_vol, 1)
        ask_price = sum(p * q for p, q in asks) / max(ask_vol, 1)
        return (bid_price * bid_vol + ask_price * ask_vol) / max(bid_vol + ask_vol, 1)

    @staticmethod
    def order_flow_imbalance(trades: list[dict]) -> float:
        buy_vol = 0.0
        sell_vol = 0.0
        for t in trades:
            if t.get("side", "buy") == "buy":
                buy_vol += t.get("quantity", 0)
            else:
                sell_vol += t.get("quantity", 0)
        total = buy_vol + sell_vol
        if total == 0:
            return 0.0
        return (buy_vol - sell_vol) / total

    @staticmethod
    def large_order_detection(trades: list[dict], threshold: float = 10000.0) -> list[dict]:
        return [t for t in trades if t.get("quantity", 0) >= threshold]

    @staticmethod
    def flow_momentum(trades: list[dict], window: int = 10) -> float:
        if len(trades) < window:
            return 0.0
        recent = trades[-window:]
        buy_vol = sum(t.get("quantity", 0) for t in recent if t.get("side") == "buy")
        sell_vol = sum(t.get("quantity", 0) for t in recent if t.get("side") == "sell")
        if sell_vol == 0:
            return 1.0
        return buy_vol / sell_vol


@dataclass
class CrossAssetAnalysis:
    """Cross-asset correlation and analysis."""
    @staticmethod
    def correlation(asset1: list[float], asset2: list[float]) -> float:
        n = min(len(asset1), len(asset2))
        if n < 2:
            return 0.0
        x = asset1[:n]
        y = asset2[:n]
        mean_x = sum(x) / n
        mean_y = sum(y) / n
        cov = sum((a - mean_x) * (b - mean_y) for a, b in zip(x, y)) / n
        var_x = sum((a - mean_x) ** 2 for a in x) / n
        var_y = sum((b - mean_y) ** 2 for b in y) / n
        if var_x == 0 or var_y == 0:
            return 0.0
        return cov / math.sqrt(var_x * var_y)

    @staticmethod
    def correlation_matrix(assets: dict[str, list[float]]) -> dict[str, dict[str, float]]:
        matrix = {}
        for name1 in assets:
            matrix[name1] = {}
            for name2 in assets:
                matrix[name1][name2] = CrossAssetAnalysis.correlation(assets[name1], assets[name2])
        return matrix

    @staticmethod
    def beta(stock_returns: list[float], market_returns: list[float]) -> float:
        n = min(len(stock_returns), len(market_returns))
        if n < 2:
            return 0.0
        s = stock_returns[:n]
        m = market_returns[:n]
        mean_s = sum(s) / n
        mean_m = sum(m) / n
        cov = sum((a - mean_s) * (b - mean_m) for a, b in zip(s, m)) / n
        var_m = sum((b - mean_m) ** 2 for b in m) / n
        if var_m == 0:
            return 0.0
        return cov / var_m

    @staticmethod
    def alpha_beta(stock_returns: list[float], market_returns: list[float]) -> tuple[float, float]:
        beta = CrossAssetAnalysis.beta(stock_returns, market_returns)
        n = min(len(stock_returns), len(market_returns))
        if n < 2:
            return 0.0, 0.0
        s = stock_returns[:n]
        m = market_returns[:n]
        mean_s = sum(s) / n
        mean_m = sum(m) / n
        alpha = mean_s - (beta * mean_m)
        return alpha, beta

    @staticmethod
    def sharpe_ratio(returns: list[float], risk_free: float = 0.0) -> float:
        if len(returns) < 2:
            return 0.0
        excess = [r - risk_free for r in returns]
        mean = sum(excess) / len(excess)
        var = sum((r - mean) ** 2 for r in excess) / (len(excess) - 1)
        std = math.sqrt(var)
        if std == 0:
            return 0.0
        return mean / std * math.sqrt(252)

    @staticmethod
    def sortino_ratio(returns: list[float], risk_free: float = 0.0) -> float:
        if len(returns) < 2:
            return 0.0
        excess = [r - risk_free for r in returns]
        mean = sum(excess) / len(excess)
        downside = [min(0, r) for r in excess]
        downside_var = sum(d ** 2 for d in downside) / len(downside)
        downside_std = math.sqrt(downside_var)
        if downside_std == 0:
            return 0.0
        return mean / downside_std * math.sqrt(252)


@dataclass
class EconomicCalendar:
    """Economic calendar and event analysis."""
    @staticmethod
    def event_impact(events: list[dict]) -> dict[str, float]:
        impact = defaultdict(float)
        for event in events:
            event_type = event.get("type", "unknown")
            impact[event_type] += event.get("weight", 0.5)
        return dict(impact)

    @staticmethod
    def volatility_impact(event_type: str) -> float:
        impact_map = {
            "nonfarm_payrolls": 0.9,
            "inflation": 0.8,
            "gdp": 0.7,
            "interest_rate": 0.9,
            "employment": 0.6,
            "pmi": 0.5,
            "retail_sales": 0.4,
            "housing": 0.3,
            "consumer_confidence": 0.3,
            "trade_balance": 0.2,
        }
        return impact_map.get(event_type, 0.1)

    @staticmethod
    def calendar_summary(events: list[dict]) -> dict[str, Any]:
        total = len(events)
        high_impact = sum(1 for e in events if e.get("weight", 0) > 0.7)
        medium_impact = sum(1 for e in events if 0.4 < e.get("weight", 0) <= 0.7)
        low_impact = sum(1 for e in events if e.get("weight", 0) <= 0.4)
        return {
            "total_events": total,
            "high_impact": high_impact,
            "medium_impact": medium_impact,
            "low_impact": low_impact,
        }


@dataclass
class FinancialStatementAnalysis:
    """Financial statement analysis."""
    @staticmethod
    def current_ratio(total_current_assets: float, total_current_liabilities: float) -> float:
        if total_current_liabilities <= 0:
            return 0.0
        return total_current_assets / total_current_liabilities

    @staticmethod
    def debt_to_equity(total_debt: float, total_equity: float) -> float:
        if total_equity <= 0:
            return float('inf')
        return total_debt / total_equity

    @staticmethod
    def return_on_equity(net_income: float, total_equity: float) -> float:
        if total_equity <= 0:
            return 0.0
        return net_income / total_equity

    @staticmethod
    def return_on_assets(net_income: float, total_assets: float) -> float:
        if total_assets <= 0:
            return 0.0
        return net_income / total_assets

    @staticmethod
    def price_to_earnings(price: float, earnings_per_share: float) -> float:
        if earnings_per_share <= 0:
            return float('inf')
        return price / earnings_per_share

    @staticmethod
    def price_to_book(price: float, book_value_per_share: float) -> float:
        if book_value_per_share <= 0:
            return float('inf')
        return price / book_value_per_share

    @staticmethod
    def earnings_growth(current_eps: float, previous_eps: float) -> float:
        if previous_eps <= 0:
            return 0.0
        return (current_eps - previous_eps) / previous_eps

    @staticmethod
    def profit_margin(net_income: float, revenue: float) -> float:
        if revenue <= 0:
            return 0.0
        return net_income / revenue

    @staticmethod
    def operating_margin(operating_income: float, revenue: float) -> float:
        if revenue <= 0:
            return 0.0
        return operating_income / revenue

    @staticmethod
    def financial_health_score(metrics: dict[str, float]) -> float:
        score = 0.0
        count = 0
        if "current_ratio" in metrics:
            cr = metrics["current_ratio"]
            if cr >= 1.5:
                score += 1.0
            elif cr >= 1.0:
                score += 0.5
            count += 1
        if "debt_to_equity" in metrics:
            de = metrics["debt_to_equity"]
            if de < 0.5:
                score += 1.0
            elif de < 1.0:
                score += 0.5
            count += 1
        if "profit_margin" in metrics:
            pm = metrics["profit_margin"]
            if pm > 0.1:
                score += 1.0
            elif pm > 0:
                score += 0.5
            count += 1
        return score / count if count > 0 else 0.0


@dataclass
class PortfolioAnalytics:
    """Portfolio analytics."""
    @staticmethod
    def portfolio_variance(weights: list[float], cov_matrix: list[list[float]]) -> float:
        total = 0.0
        for i in range(len(weights)):
            for j in range(len(weights)):
                total += weights[i] * weights[j] * cov_matrix[i][j]
        return total

    @staticmethod
    def portfolio_volatility(weights: list[float], cov_matrix: list[list[float]]) -> float:
        var = PortfolioAnalytics.portfolio_variance(weights, cov_matrix)
        return math.sqrt(var) if var > 0 else 0.0

    @staticmethod
    def portfolio_return(weights: list[float], expected_returns: list[float]) -> float:
        return sum(w * r for w, r in zip(weights, expected_returns))

    @staticmethod
    def risk_contribution(weights: list[float], cov_matrix: list[list[float]]) -> list[float]:
        port_var = PortfolioAnalytics.portfolio_variance(weights, cov_matrix)
        if port_var <= 0:
            return [0.0] * len(weights)
        contributions = []
        for i in range(len(weights)):
            margin = 0.0
            for j in range(len(weights)):
                margin += weights[j] * cov_matrix[i][j]
            contributions.append(weights[i] * margin / port_var)
        return contributions

    @staticmethod
    def sharpe_ratio(portfolio_returns: list[float], risk_free: float = 0.0) -> float:
        if len(portfolio_returns) < 2:
            return 0.0
        excess = [r - risk_free for r in portfolio_returns]
        mean = sum(excess) / len(excess)
        var = sum((r - mean) ** 2 for r in excess) / (len(excess) - 1)
        std = math.sqrt(var)
        if std == 0:
            return 0.0
        return mean / std * math.sqrt(252)

    @staticmethod
    def sortino_ratio(portfolio_returns: list[float], risk_free: float = 0.0) -> float:
        if len(portfolio_returns) < 2:
            return 0.0
        excess = [r - risk_free for r in portfolio_returns]
        mean = sum(excess) / len(excess)
        downside = [min(0, r) for r in excess]
        downside_var = sum(d ** 2 for d in downside) / len(downside)
        downside_std = math.sqrt(downside_var)
        if downside_std == 0:
            return 0.0
        return mean / downside_std * math.sqrt(252)

    @staticmethod
    def value_at_risk(returns: list[float], confidence: float = 0.95) -> float:
        if not returns:
            return 0.0
        sorted_returns = sorted(returns)
        index = int((1 - confidence) * len(sorted_returns))
        return sorted_returns[index]

    @staticmethod
    def conditional_var(returns: list[float], confidence: float = 0.95) -> float:
        if not returns:
            return 0.0
        sorted_returns = sorted(returns)
        index = int((1 - confidence) * len(sorted_returns))
        var = sorted_returns[index]
        tail = sorted_returns[:index] if index > 0 else [sorted_returns[0]]
        return sum(tail) / len(tail)

    @staticmethod
    def max_drawdown(values: list[float]) -> float:
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

    @staticmethod
    def cagr(initial: float, final: float, years: float) -> float:
        if initial <= 0 or years <= 0:
            return 0.0
        return (final / initial) ** (1 / years) - 1

    @staticmethod
    def treynor_ratio(returns: list[float], market_returns: list[float]) -> float:
        n = min(len(returns), len(market_returns))
        if n < 2:
            return 0.0
        beta = CrossAssetAnalysis.beta(returns, market_returns)
        if beta == 0:
            return 0.0
        excess = [r for r in returns[:n]]
        mean = sum(excess) / n
        return mean / beta


@dataclass
class MarketRegimeDetector:
    """Market regime detection."""
    @staticmethod
    def detect_regime(prices: list[float], window: int = 50) -> str:
        if len(prices) < window:
            return "unknown"
        recent = prices[-window:]
        returns = [recent[i] / recent[i-1] - 1 for i in range(1, len(recent))]
        mean = sum(returns) / len(returns)
        var = sum((r - mean) ** 2 for r in returns) / len(returns)
        vol = math.sqrt(var) * math.sqrt(252)
        if mean > 0 and vol < 0.15:
            return "bull_calm"
        elif mean > 0 and vol >= 0.15:
            return "bull_volatile"
        elif mean < 0 and vol < 0.15:
            return "bear_calm"
        elif mean < 0 and vol >= 0.15:
            return "bear_volatile"
        else:
            return "neutral"

    @staticmethod
    def regime_transition(prev_regime: str, curr_regime: str) -> bool:
        return prev_regime != curr_regime

    @staticmethod
    def regime_strength(prices: list[float], window: int = 50) -> float:
        if len(prices) < window:
            return 0.0
        recent = prices[-window:]
        returns = [recent[i] / recent[i-1] - 1 for i in range(1, len(recent))]
        mean = sum(returns) / len(returns)
        return abs(mean) * len(returns)

    @staticmethod
    def volatility_regime(volatility: float) -> str:
        if volatility < 0.10:
            return "low_vol"
        elif volatility < 0.25:
            return "normal_vol"
        elif volatility < 0.40:
            return "high_vol"
        else:
            return "extreme_vol"
