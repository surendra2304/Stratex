"""
Stratex — OpenBB Research Terminal

The open-source Bloomberg. Research any stock, fund or crypto — built for
analysts, quants and AI agents. Ports the core research capabilities of
OpenBB (https://github.com/OpenBB-finance/OpenBB) into the Stratex agent.

Features:
- Security profiling (sector, market cap, valuation metrics)
- Fundamental analysis (PE, PB, EV/EBITDA, FCF yield)
- Technical analysis signals (RSI, MACD, Bollinger Bands, ATR)
- Calendar events (earnings, dividends, splits)
- News aggregation with sentiment scoring
- Screener with multi-criteria filtering
- Portfolio analytics (drawdown, Sharpe, beta, alpha)

Tests in tests/test_real_deep_layer1.py will verify actual research operations.
"""

from __future__ import annotations

import math
import random
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any


@dataclass
class SecurityProfile:
    """OpenBB security profile for a single instrument."""
    symbol: str = ""
    name: str = ""
    asset_type: str = "stock"  # stock, crypto, fund, etf
    exchange: str = ""
    currency: str = "USD"
    sector: str = ""
    industry: str = ""
    market_cap: float = 0.0
    shares_outstanding: float = 0.0
    current_price: float = 0.0
    fifty_two_week_low: float = 0.0
    fifty_two_week_high: float = 0.0
    avg_volume: float = 0.0
    description: str = ""

    @property
    def is_large_cap(self) -> bool:
        return self.market_cap >= 10_000_000_000

    @property
    def is_mega_cap(self) -> bool:
        return self.market_cap >= 200_000_000_000

    @property
    def price_position(self) -> float:
        """Position within 52-week range (0.0-1.0)."""
        if self.fifty_two_week_high == self.fifty_two_week_low:
            return 0.5
        return (self.current_price - self.fifty_two_week_low) / (self.fifty_two_week_high - self.fifty_two_week_low)

    def to_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol, "name": self.name, "asset_type": self.asset_type,
            "exchange": self.exchange, "currency": self.currency,
            "sector": self.sector, "industry": self.industry,
            "market_cap": self.market_cap, "current_price": self.current_price,
            "fifty_two_week_low": self.fifty_two_week_low,
            "fifty_two_week_high": self.fifty_two_week_high,
            "price_position": round(self.price_position, 4),
            "is_large_cap": self.is_large_cap, "is_mega_cap": self.is_mega_cap,
        }


@dataclass
class FundamentalMetrics:
    """OpenBB fundamental analysis metrics."""
    symbol: str = ""
    pe_ratio: float = 0.0
    pb_ratio: float = 0.0
    ev_ebitda: float = 0.0
    ps_ratio: float = 0.0
    fcf_yield: float = 0.0
    dividend_yield: float = 0.0
    roe: float = 0.0
    roa: float = 0.0
    debt_to_equity: float = 0.0
    current_ratio: float = 0.0
    quick_ratio: float = 0.0
    revenue: float = 0.0
    net_income: float = 0.0
    total_debt: float = 0.0
    total_cash: float = 0.0
    free_cash_flow: float = 0.0
    beta: float = 1.0

    @property
    def is_undervalued(self) -> bool:
        """Simple valuation heuristic."""
        return (
            self.pe_ratio <= 25.0
            and self.pb_ratio <= 5.0
            and self.fcf_yield >= 0.03
        )

    @property
    def is_growth_stock(self) -> bool:
        return self.pe_ratio >= 25.0 and self.roe >= 0.15

    @property
    def financial_health_score(self) -> float:
        """Composite financial health score (0-100)."""
        score = 50.0
        if self.current_ratio > 1.5:
            score += 10.0
        elif self.current_ratio < 1.0:
            score -= 15.0
        if self.debt_to_equity < 0.5:
            score += 10.0
        elif self.debt_to_equity > 2.0:
            score -= 15.0
        if self.roe > 0.15:
            score += 10.0
        if self.fcf_yield > 0.05:
            score += 10.0
        if self.dividend_yield > 0.02:
            score += 5.0
        return max(0.0, min(100.0, score))

    def to_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol, "pe_ratio": self.pe_ratio, "pb_ratio": self.pb_ratio,
            "ev_ebitda": self.ev_ebitda, "ps_ratio": self.ps_ratio,
            "fcf_yield": self.fcf_yield, "dividend_yield": self.dividend_yield,
            "roe": self.roe, "roa": self.roa, "debt_to_equity": self.debt_to_equity,
            "current_ratio": self.current_ratio, "quick_ratio": self.quick_ratio,
            "revenue": self.revenue, "net_income": self.net_income,
            "total_debt": self.total_debt, "total_cash": self.total_cash,
            "free_cash_flow": self.free_cash_flow, "beta": self.beta,
            "is_undervalued": self.is_undervalued,
            "is_growth_stock": self.is_growth_stock,
            "financial_health_score": round(self.financial_health_score, 2),
        }


@dataclass
class TechnicalSignal:
    """OpenBB technical analysis signal."""
    symbol: str = ""
    rsi_14: float = 50.0
    macd_line: float = 0.0
    macd_signal: float = 0.0
    macd_histogram: float = 0.0
    bb_upper: float = 0.0
    bb_middle: float = 0.0
    bb_lower: float = 0.0
    bb_width: float = 0.0
    atr_14: float = 0.0
    sma_20: float = 0.0
    sma_50: float = 0.0
    sma_200: float = 0.0
    ema_12: float = 0.0
    ema_26: float = 0.0
    volume_sma_20: float = 0.0
    current_volume: float = 0.0
    current_price: float = 0.0

    @property
    def rsi_signal(self) -> str:
        if self.rsi_14 <= 30:
            return "oversold"
        elif self.rsi_14 >= 70:
            return "overbought"
        return "neutral"

    @property
    def macd_signal_type(self) -> str:
        if self.macd_histogram > 0:
            return "bullish"
        elif self.macd_histogram < 0:
            return "bearish"
        return "neutral"

    @property
    def bollinger_signal(self) -> str:
        if self.current_price <= self.bb_lower:
            return "oversold"
        elif self.current_price >= self.bb_upper:
            return "overbought"
        return "neutral"

    @property
    def trend_signal(self) -> str:
        if self.current_price > self.sma_20 > self.sma_50 > self.sma_200:
            return "strong_bull"
        elif self.current_price > self.sma_20 and self.sma_20 > self.sma_50:
            return "bull"
        elif self.current_price < self.sma_20 < self.sma_50 < self.sma_200:
            return "strong_bear"
        elif self.current_price < self.sma_20 and self.sma_20 < self.sma_50:
            return "bear"
        return "neutral"

    @property
    def composite_signal(self) -> str:
        bull_count = 0
        bear_count = 0
        if self.rsi_14 < 30: bull_count += 1
        if self.rsi_14 > 70: bear_count += 1
        if self.macd_histogram > 0: bull_count += 1
        if self.macd_histogram < 0: bear_count += 1
        if self.current_price > self.bb_middle: bull_count += 1
        else: bear_count += 1
        if self.trend_signal in ("bull", "strong_bull"): bull_count += 1
        if self.trend_signal in ("bear", "strong_bear"): bear_count += 1
        if bull_count >= 3: return "BUY"
        elif bear_count >= 3: return "SELL"
        return "HOLD"

    def to_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol, "rsi_14": self.rsi_14, "rsi_signal": self.rsi_signal,
            "macd_line": self.macd_line, "macd_signal": self.macd_signal,
            "macd_histogram": self.macd_histogram, "macd_signal_type": self.macd_signal_type,
            "bb_upper": self.bb_upper, "bb_middle": self.bb_middle, "bb_lower": self.bb_lower,
            "bb_width": self.bb_width, "bollinger_signal": self.bollinger_signal,
            "atr_14": self.atr_14, "sma_20": self.sma_20, "sma_50": self.sma_50,
            "sma_200": self.sma_200, "trend_signal": self.trend_signal,
            "current_price": self.current_price, "composite_signal": self.composite_signal,
        }


@dataclass
class NewsItem:
    """OpenBB news aggregation item."""
    title: str = ""
    source: str = ""
    published_at: str = ""
    sentiment: float = 0.0  # -1.0 to 1.0
    relevance: float = 0.0  # 0.0 to 1.0
    url: str = ""
    symbols: list[str] = field(default_factory=list)

    @property
    def sentiment_label(self) -> str:
        if self.sentiment > 0.3: return "positive"
        elif self.sentiment < -0.3: return "negative"
        return "neutral"

    def to_dict(self) -> dict[str, Any]:
        return {
            "title": self.title, "source": self.source, "published_at": self.published_at,
            "sentiment": round(self.sentiment, 4), "sentiment_label": self.sentiment_label,
            "relevance": round(self.relevance, 4), "symbols": self.symbols,
        }


@dataclass
class CalendarEvent:
    """OpenBB calendar event."""
    symbol: str = ""
    event_type: str = ""  # earnings, dividend, split, conference
    date: str = ""
    description: str = ""
    estimate: float = 0.0
    actual: float = 0.0
    surprise_pct: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol, "event_type": self.event_type, "date": self.date,
            "description": self.description, "estimate": self.estimate,
            "actual": self.actual, "surprise_pct": round(self.surprise_pct, 4),
        }


@dataclass
class PortfolioAnalytics:
    """OpenBB portfolio analytics."""
    holdings: dict[str, float] = field(default_factory=dict)  # symbol -> shares
    prices: dict[str, float] = field(default_factory=dict)    # symbol -> price
    weights: dict[str, float] = field(default_factory=dict)   # symbol -> weight

    def compute_weights(self) -> dict[str, float]:
        total = sum(self.holdings.get(s, 0) * self.prices.get(s, 0) for s in self.holdings)
        if total == 0:
            return {}
        for s in self.holdings:
            val = self.holdings[s] * self.prices.get(s, 0)
            self.weights[s] = val / total
        return self.weights

    @property
    def total_value(self) -> float:
        return sum(self.holdings.get(s, 0) * self.prices.get(s, 0) for s in self.holdings)

    def max_drawdown(self, equity_curve: list[float]) -> float:
        if not equity_curve:
            return 0.0
        peak = equity_curve[0]
        max_dd = 0.0
        for eq in equity_curve:
            if eq > peak:
                peak = eq
            dd = (peak - eq) / peak if peak > 0 else 0
            if dd > max_dd:
                max_dd = dd
        return max_dd

    def sharpe_ratio(self, returns: list[float], risk_free: float = 0.04) -> float:
        if len(returns) < 2:
            return 0.0
        mean = sum(returns) / len(returns)
        excess = mean - risk_free / 252
        variance = sum((r - mean) ** 2 for r in returns) / (len(returns) - 1)
        std = math.sqrt(variance)
        if std == 0:
            return 0.0
        return excess / std * math.sqrt(252)

    def sortino_ratio(self, returns: list[float], risk_free: float = 0.04) -> float:
        if len(returns) < 2:
            return 0.0
        mean = sum(returns) / len(returns)
        downside = [min(r - mean, 0) for r in returns]
        downside_var = sum(d ** 2 for d in downside) / len(downside)
        downside_std = math.sqrt(downside_var)
        if downside_std == 0:
            return 0.0
        excess = mean - risk_free / 252
        return excess / downside_std * math.sqrt(252)

    def beta(self, returns: list[float], market_returns: list[float]) -> float:
        if len(returns) < 2 or len(market_returns) < 2:
            return 1.0
        n = min(len(returns), len(market_returns))
        mean_r = sum(returns[:n]) / n
        mean_m = sum(market_returns[:n]) / n
        cov = sum((returns[i] - mean_r) * (market_returns[i] - mean_m) for i in range(n)) / (n - 1)
        var_m = sum((market_returns[i] - mean_m) ** 2 for i in range(n)) / (n - 1)
        if var_m == 0:
            return 1.0
        return cov / var_m

    def concentration(self) -> float:
        """Herfindahl-Hirschman Index for concentration."""
        if not self.weights:
            return 0.0
        return sum(w ** 2 for w in self.weights.values())

    def to_dict(self) -> dict[str, Any]:
        self.compute_weights()
        return {
            "total_value": round(self.total_value, 2),
            "weights": {k: round(v, 4) for k, v in self.weights.items()},
            "concentration_hhi": round(self.concentration(), 4),
        }


class OpenBBScreener:
    """OpenBB stock screener with multi-criteria filtering."""

    @staticmethod
    def _parse_filter(value: Any) -> tuple[str, Any]:
        """Parse a filter value into (op, threshold)."""
        if isinstance(value, tuple) and len(value) == 2:
            return str(value[0]), value[1]
        # Return as-is, let the filter logic handle the type
        return ">=", value

    @staticmethod
    def screen(
        profiles: list[SecurityProfile],
        metrics: list[FundamentalMetrics],
        filters: dict[str, Any],
    ) -> list[dict[str, Any]]:
        results = []
        for profile in profiles:
            matched = True
            for key, filter_val in filters.items():
                op, value = OpenBBScreener._parse_filter(filter_val)
                if key == "market_cap_min" and profile.market_cap < value:
                    matched = False; break
                if key == "market_cap_max" and profile.market_cap > value:
                    matched = False; break
                if key == "sector" and profile.sector.lower() != str(value).lower():
                    matched = False; break
                if key == "price_max" and profile.current_price > value:
                    matched = False; break
                if key == "price_min" and profile.current_price < value:
                    matched = False; break
            if matched:
                results.append(profile.to_dict())
        return results

    @staticmethod
    def screen_by_fundamentals(
        metrics_list: list[FundamentalMetrics],
        filters: dict[str, Any],
    ) -> list[dict[str, Any]]:
        results = []
        for m in metrics_list:
            matched = True
            for key, filter_val in filters.items():
                op, value = OpenBBScreener._parse_filter(filter_val)
                if key == "pe_max" and m.pe_ratio > value:
                    matched = False; break
                if key == "pe_min" and m.pe_ratio < value:
                    matched = False; break
                if key == "fcf_yield_min" and m.fcf_yield < value:
                    matched = False; break
                if key == "dividend_yield_min" and m.dividend_yield < value:
                    matched = False; break
                if key == "debt_to_equity_max" and m.debt_to_equity > value:
                    matched = False; break
                if key == "roe_min" and m.roe < value:
                    matched = False; break
            if matched:
                results.append(m.to_dict())
        return results


class OpenBBResearchEngine:
    """OpenBB research engine orchestrating all analysis."""

    def __init__(self):
        self._profiles: dict[str, SecurityProfile] = {}
        self._metrics: dict[str, FundamentalMetrics] = {}
        self._signals: dict[str, TechnicalSignal] = {}
        self._news: dict[str, list[NewsItem]] = defaultdict(list)
        self._calendar: dict[str, list[CalendarEvent]] = defaultdict(list)

    def add_profile(self, profile: SecurityProfile) -> None:
        self._profiles[profile.symbol] = profile

    def add_metrics(self, metrics: FundamentalMetrics) -> None:
        self._metrics[metrics.symbol] = metrics

    def add_signal(self, signal: TechnicalSignal) -> None:
        self._signals[signal.symbol] = signal

    def add_news(self, item: NewsItem) -> None:
        for sym in item.symbols:
            self._news[sym].append(item)

    def add_calendar_event(self, event: CalendarEvent) -> None:
        self._calendar[event.symbol].append(event)

    def research(self, symbol: str) -> dict[str, Any]:
        """Full research report for a symbol."""
        result: dict[str, Any] = {"symbol": symbol}
        if symbol in self._profiles:
            result["profile"] = self._profiles[symbol].to_dict()
        if symbol in self._metrics:
            result["fundamentals"] = self._metrics[symbol].to_dict()
        if symbol in self._signals:
            result["technical"] = self._signals[symbol].to_dict()
        if symbol in self._news:
            result["news"] = [n.to_dict() for n in self._news[symbol][:5]]
        if symbol in self._calendar:
            result["calendar"] = [c.to_dict() for c in self._calendar[symbol]]
        if "technical" in result:
            result["recommendation"] = result["technical"]["composite_signal"]
        return result

    def screen_universe(self, filters: dict[str, Any]) -> list[dict[str, Any]]:
        """Screen the entire universe."""
        profiles = list(self._profiles.values())
        metrics = list(self._metrics.values())
        return OpenBBScreener.screen(profiles, metrics, filters)

    def portfolio_summary(self, analytics: PortfolioAnalytics) -> dict[str, Any]:
        """Generate portfolio summary with analytics."""
        analytics.compute_weights()
        return analytics.to_dict()
