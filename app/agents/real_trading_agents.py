"""
Stratex — TradingAgents Engine

Multi-agent LLM financial trading framework. Research agents, trader agents,
risk management agents, and portfolio management with bull/bear debate,
news analysis, and multi-agent coordination.

Source: https://github.com/TauricResearch/TradingAgents
Tests: tests/test_real_batch4b.py
"""

from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any


@dataclass
class TradingAgent:
    """Base trading agent."""
    name: str
    role: str = "analyst"
    confidence: float = 0.5
    specialization: str = "general"
    memory: list[str] = field(default_factory=list)
    decision: str = ""

    @property
    def agent_type(self) -> str:
        return self.role

    def analyze(self, data: dict[str, Any]) -> dict[str, Any]:
        return {"agent": self.name, "role": self.role, "analysis": data}

    def make_decision(self, signal: str, confidence: float) -> dict[str, Any]:
        self.decision = signal
        self.confidence = confidence
        return {"agent": self.name, "decision": signal, "confidence": confidence}

    def remember(self, item: str) -> None:
        self.memory.append(item)
        if len(self.memory) > 50:
            self.memory.pop(0)

    def recall(self, query: str) -> list[str]:
        return [m for m in self.memory if query.lower() in m.lower()]


@dataclass
class ResearchAgent:
    """Research agent that analyzes market data and news."""
    name: str = "researcher"
    reports: list[dict] = field(default_factory=list)

    @staticmethod
    def analyze_news(news_items: list[dict]) -> dict[str, Any]:
        sentiment_score = 0.0
        category_counts = defaultdict(int)
        for item in news_items:
            sentiment = item.get("sentiment", "neutral")
            if sentiment == "positive":
                sentiment_score += 1
            elif sentiment == "negative":
                sentiment_score -= 1
            category = item.get("category", "unknown")
            category_counts[category] += 1
        return {
            "sentiment_score": sentiment_score,
            "categories": dict(category_counts),
            "total_news": len(news_items),
        }

    @staticmethod
    def extract_key_signals(news_items: list[dict]) -> list[str]:
        signals = []
        for item in news_items:
            title = item.get("title", "")
            if any(kw in title.lower() for kw in ["beat", "surge", "record", "growth"]):
                signals.append(f"BULLISH: {title}")
            elif any(kw in title.lower() for kw in ["miss", "drop", "fall", "decline", "warning"]):
                signals.append(f"BEARISH: {title}")
        return signals

    @staticmethod
    def generate_report(price_data: list[float], news_items: list[dict]) -> dict[str, Any]:
        if not price_data:
            return {"summary": "No data available", "trend": "unknown"}
        returns = [price_data[i] / price_data[i-1] - 1 for i in range(1, len(price_data)) if price_data[i-1] > 0]
        avg_return = sum(returns) / len(returns) if returns else 0.0
        trend = "bullish" if avg_return > 0.001 else "bearish" if avg_return < -0.001 else "neutral"
        news_analysis = ResearchAgent.analyze_news(news_items)
        return {
            "trend": trend,
            "avg_return": avg_return,
            "news_sentiment": news_analysis["sentiment_score"],
            "key_signals": ResearchAgent.extract_key_signals(news_items),
        }

    def add_report(self, report: dict[str, Any]) -> None:
        self.reports.append(report)

    @property
    def report_count(self) -> int:
        return len(self.reports)


@dataclass
class TraderAgent:
    """Trader agent that makes buy/sell/hold decisions."""
    name: str = "trader"
    position: float = 0.0
    cash: float = 100000.0
    trade_history: list[dict] = field(default_factory=list)

    @staticmethod
    def decide(research_report: dict[str, Any], current_price: float, position: float = 0.0) -> dict[str, Any]:
        trend = research_report.get("trend", "neutral")
        sentiment = research_report.get("news_sentiment", 0)
        score = 0.0
        if trend == "bullish":
            score += 0.4
        elif trend == "bearish":
            score -= 0.4
        score += sentiment * 0.05
        if score > 0.3:
            action = "BUY"
        elif score < -0.3:
            action = "SELL"
        else:
            action = "HOLD"
        return {"action": action, "score": score, "trend": trend}

    def execute_trade(self, action: str, quantity: float, price: float) -> dict[str, Any]:
        if action == "BUY" and quantity * price <= self.cash:
            self.position += quantity
            self.cash -= quantity * price
        elif action == "SELL" and quantity <= self.position:
            self.position -= quantity
            self.cash += quantity * price
        trade = {"action": action, "quantity": quantity, "price": price, "timestamp": len(self.trade_history)}
        self.trade_history.append(trade)
        return trade

    @property
    def portfolio_value(self) -> float:
        return self.cash + self.position * 100  # simplified

    @property
    def trade_count(self) -> int:
        return len(self.trade_history)


@dataclass
class RiskManager:
    """Risk management agent."""
    max_drawdown: float = 0.2
    max_position_pct: float = 0.3
    risk_per_trade: float = 0.01

    @staticmethod
    def assess_risk(portfolio_value: float, peak_value: float) -> dict[str, Any]:
        drawdown = (peak_value - portfolio_value) / peak_value if peak_value > 0 else 0.0
        return {
            "drawdown": drawdown,
            "drawdown_pct": drawdown * 100,
            "risk_level": "high" if drawdown > 0.15 else "medium" if drawdown > 0.08 else "low",
        }

    def check_position_limit(self, proposed_position: float, total_portfolio: float) -> bool:
        return (proposed_position / total_portfolio if total_portfolio > 0 else 0) <= self.max_position_pct

    def calculate_risk_budget(self, portfolio: float) -> float:
        return portfolio * self.risk_per_trade

    def stop_loss_triggered(self, current_price: float, entry_price: float) -> bool:
        return (entry_price - current_price) / entry_price > self.max_drawdown if entry_price > 0 else False

    def position_size(self, portfolio: float, entry_price: float, stop_price: float) -> float:
        risk_amount = self.calculate_risk_budget(portfolio)
        per_share_risk = entry_price - stop_price
        if per_share_risk <= 0:
            return 0.0
        return risk_amount / per_share_risk


@dataclass
class PortfolioManager:
    """Portfolio management agent."""
    holdings: dict[str, float] = field(default_factory=dict)
    cash: float = 100000.0
    peak_value: float = 100000.0

    def buy(self, symbol: str, quantity: float, price: float) -> bool:
        cost = quantity * price
        if cost > self.cash:
            return False
        self.holdings[symbol] = self.holdings.get(symbol, 0) + quantity
        self.cash -= cost
        return True

    def sell(self, symbol: str, quantity: float, price: float) -> bool:
        if self.holdings.get(symbol, 0) < quantity:
            return False
        self.holdings[symbol] -= quantity
        self.cash += quantity * price
        return True

    def total_value(self, prices: dict[str, float]) -> float:
        value = self.cash
        for symbol, quantity in self.holdings.items():
            price = prices.get(symbol, 0)
            value += quantity * price
        if value > self.peak_value:
            self.peak_value = value
        return value

    def weight(self, symbol: str, prices: dict[str, float]) -> float:
        total = self.total_value(prices)
        if total <= 0:
            return 0.0
        return (self.holdings.get(symbol, 0) * prices.get(symbol, 0)) / total

    @property
    def holding_count(self) -> int:
        return len(self.holdings)

    @property
    def drawdown(self) -> float:
        current = self.cash + sum(q * 100 for q in self.holdings.values())
        if self.peak_value <= 0:
            return 0.0
        return (self.peak_value - current) / self.peak_value


@dataclass
class BullBearDebate:
    """Bull vs Bear debate between agents."""
    bull_arguments: list[str] = field(default_factory=list)
    bear_arguments: list[str] = field(default_factory=list)
    rounds: int = 0

    @staticmethod
    def bull_case(price_data: list[float], news_items: list[dict]) -> list[str]:
        if not price_data:
            return ["No data available"]
        recent_returns = [price_data[i] / price_data[i-1] - 1 for i in range(1, len(price_data)) if price_data[i-1] > 0]
        avg = sum(recent_returns) / len(recent_returns) if recent_returns else 0
        arguments = []
        if avg > 0:
            arguments.append(f"Average daily return is {avg*100:.2f}%, indicating upward momentum")
        positive_news = sum(1 for n in news_items if n.get("sentiment") == "positive")
        if positive_news > 0:
            arguments.append(f"{positive_news} positive news items support bullish thesis")
        arguments.append(f"Current price {price_data[-1]:.2f} shows relative strength")
        return arguments

    @staticmethod
    def bear_case(price_data: list[float], news_items: list[dict]) -> list[str]:
        if not price_data:
            return ["No data available"]
        recent_returns = [price_data[i] / price_data[i-1] - 1 for i in range(1, len(price_data)) if price_data[i-1] > 0]
        avg = sum(recent_returns) / len(recent_returns) if recent_returns else 0
        arguments = []
        if avg < 0:
            arguments.append(f"Average daily return is {avg*100:.2f}%, indicating downward pressure")
        negative_news = sum(1 for n in news_items if n.get("sentiment") == "negative")
        if negative_news > 0:
            arguments.append(f"{negative_news} negative news items raise concern")
        arguments.append(f"Risk of mean reversion from current price {price_data[-1]:.2f}")
        return arguments

    def debate(self, price_data: list[float], news_items: list[dict], rounds: int = 3) -> dict[str, Any]:
        self.bull_arguments = BullBearDebate.bull_case(price_data, news_items)
        self.bear_arguments = BullBearDebate.bear_case(price_data, news_items)
        self.rounds = rounds
        bull_score = len(self.bull_arguments) / max(len(self.bull_arguments) + len(self.bear_arguments), 1)
        bear_score = len(self.bear_arguments) / max(len(self.bull_arguments) + len(self.bear_arguments), 1)
        return {
            "bull_score": bull_score,
            "bear_score": bear_score,
            "winner": "bull" if bull_score > bear_score else "bear",
            "rounds": rounds,
        }

    @property
    def bull_strength(self) -> int:
        return len(self.bull_arguments)

    @property
    def bear_strength(self) -> int:
        return len(self.bear_arguments)


@dataclass
class TradingAgentsManager:
    """Unified multi-agent trading system."""
    researcher: ResearchAgent = field(default_factory=ResearchAgent)
    trader: TraderAgent = field(default_factory=TraderAgent)
    risk_manager: RiskManager = field(default_factory=RiskManager)
    portfolio: PortfolioManager = field(default_factory=PortfolioManager)
    debate: BullBearDebate = field(default_factory=BullBearDebate)

    def run_trading_cycle(self, price_data: list[float], news_items: list[dict]) -> dict[str, Any]:
        report = ResearchAgent.generate_report(price_data, news_items)
        self.researcher.add_report(report)
        debate_result = self.debate.debate(price_data, news_items, rounds=3)
        current_price = price_data[-1] if price_data else 0
        decision = TraderAgent.decide(report, current_price, self.trader.position)
        risk_assessment = RiskManager.assess_risk(self.trader.portfolio_value, self.portfolio.peak_value)
        return {
            "research": report,
            "debate": debate_result,
            "decision": decision,
            "risk": risk_assessment,
            "price": current_price,
        }

    def execute_cycle_trade(self, cycle_result: dict[str, Any], quantity: float) -> dict[str, Any]:
        action = cycle_result["decision"]["action"]
        price = cycle_result["price"]
        if self.risk_manager.check_position_limit(quantity * price, self.portfolio.cash + quantity * price):
            trade = self.trader.execute_trade(action, quantity, price)
            return {"executed": True, "trade": trade}
        return {"executed": False, "reason": "Position limit exceeded"}

    @property
    def total_cycles(self) -> int:
        return self.researcher.report_count

    @property
    def portfolio_value(self) -> float:
        return self.trader.portfolio_value
