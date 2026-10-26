"""
Stratex — Polymarket API Engine

Prediction market API. Markets, outcomes, prices, orders, positions,
resolution, and user activity.

Source: https://github.com/Polymarket/api (prediction market concepts)
Tests: tests/test_real_batch4b.py
"""

from __future__ import annotations

import hashlib
import time
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

# Real Polymarket CLOB integration
try:
    import py_clob_client
    HAS_REAL_CLOB = True
except ImportError:
    py_clob_client = None
    HAS_REAL_CLOB = False



@dataclass
class PredictionMarket:
    """A prediction market."""
    market_id: str
    question: str
    outcomes: list[str] = field(default_factory=list)
    outcome_prices: list[float] = field(default_factory=list)
    active: bool = True
    closed: bool = False
    resolved: bool = False
    end_date: float = 0.0
    volume: float = 0.0
    liq_min: float = 0.0
    liq_max: float = 0.0
    created_at: float = 0.0

    @staticmethod
    def create_market(question: str, outcomes: list[str]) -> PredictionMarket:
        mid = hashlib.md5(question.encode()).hexdigest()[:12]
        prices = [1.0 / len(outcomes)] * len(outcomes)
        return PredictionMarket(
            market_id=f"market-{mid}",
            question=question,
            outcomes=outcomes,
            outcome_prices=prices,
            created_at=time.time(),
        )

    @property
    def outcome_count(self) -> int:
        return len(self.outcomes)

    @property
    def implied_probabilities(self) -> dict[str, float]:
        result = {}
        for outcome, price in zip(self.outcomes, self.outcome_prices):
            result[outcome] = price
        return result

    @property
    def market_sum(self) -> float:
        return sum(self.outcome_prices)

    @property
    def is_binary(self) -> bool:
        return len(self.outcomes) == 2

    @property
    def favorite_outcome(self) -> str:
        if not self.outcomes:
            return ""
        max_idx = self.outcome_prices.index(max(self.outcome_prices))
        return self.outcomes[max_idx]

    def update_price(self, outcome_index: int, price: float) -> None:
        if 0 <= outcome_index < len(self.outcome_prices):
            self.outcome_prices[outcome_index] = max(0.0, min(1.0, price))

    def resolve(self, winning_outcome: str) -> None:
        if winning_outcome in self.outcomes:
            idx = self.outcomes.index(winning_outcome)
            self.outcome_prices = [0.0] * len(self.outcomes)
            self.outcome_prices[idx] = 1.0
            self.resolved = True
            self.closed = True

    @property
    def total_volume(self) -> float:
        return self.volume


@dataclass
class Order:
    """Prediction market order."""
    order_id: str
    market_id: str
    outcome: str
    side: str = "BUY"
    size: float = 1.0
    price: float = 0.5
    status: str = "open"
    filled: float = 0.0
    timestamp: float = 0.0

    @staticmethod
    def create_order(market_id: str, outcome: str, side: str, size: float, price: float) -> Order:
        oid = hashlib.md5(f"{market_id}:{outcome}:{side}:{size}".encode()).hexdigest()[:12]
        return Order(
            order_id=f"order-{oid}",
            market_id=market_id,
            outcome=outcome,
            side=side,
            size=size,
            price=price,
            timestamp=time.time(),
        )

    @property
    def notional(self) -> float:
        return self.size * self.price

    @property
    def remaining(self) -> float:
        return max(self.size - self.filled, 0.0)

    @property
    def is_filled(self) -> bool:
        return self.filled >= self.size

    def fill(self, amount: float) -> None:
        self.filled += amount
        if self.filled >= self.size:
            self.status = "filled"

    def cancel(self) -> None:
        self.status = "cancelled"


@dataclass
class Position:
    """Prediction market position."""
    market_id: str
    outcome: str
    quantity: float = 0.0
    avg_price: float = 0.0
    realized_pnl: float = 0.0

    @staticmethod
    def open_position(market_id: str, outcome: str, quantity: float, price: float) -> Position:
        return Position(market_id=market_id, outcome=outcome, quantity=quantity, avg_price=price)

    @property
    def cost_basis(self) -> float:
        return self.quantity * self.avg_price

    def market_value(self, current_price: float) -> float:
        return self.quantity * current_price

    def unrealized_pnl(self, current_price: float) -> float:
        return self.quantity * (current_price - self.avg_price)

    def close(self, current_price: float) -> dict[str, Any]:
        pnl = self.quantity * (current_price - self.avg_price)
        self.realized_pnl += pnl
        self.quantity = 0.0
        return {"pnl": pnl, "realized_pnl": self.realized_pnl}

    @property
    def roi(self, current_price: float) -> float:
        if self.avg_price <= 0:
            return 0.0
        return (current_price - self.avg_price) / self.avg_price


@dataclass
class PolymarketAPI:
    """Polymarket prediction market API manager."""
    markets: dict[str, PredictionMarket] = field(default_factory=dict)
    orders: dict[str, Order] = field(default_factory=dict)
    positions: dict[str, Position] = field(default_factory=dict)

    def create_market(self, question: str, outcomes: list[str]) -> PredictionMarket:
        market = PredictionMarket.create_market(question, outcomes)
        self.markets[market.market_id] = market
        return market

    def get_market(self, market_id: str) -> PredictionMarket | None:
        return self.markets.get(market_id)

    def list_markets(self, active_only: bool = True) -> list[PredictionMarket]:
        if active_only:
            return [m for m in self.markets.values() if m.active and not m.closed]
        return list(self.markets.values())

    def place_order(self, market_id: str, outcome: str, side: str, size: float, price: float) -> Order:
        order = Order.create_order(market_id, outcome, side, size, price)
        self.orders[order.order_id] = order
        market = self.markets.get(market_id)
        if market:
            idx = market.outcomes.index(outcome) if outcome in market.outcomes else 0
            market.update_price(idx, price)
            market.volume += size * price
        return order

    def match_order(self, order_id: str, fill_amount: float) -> bool:
        order = self.orders.get(order_id)
        if not order:
            return False
        order.fill(fill_amount)
        if order.is_filled:
            market = self.markets.get(order.market_id)
            if market:
                idx = market.outcomes.index(order.outcome) if order.outcome in market.outcomes else 0
                self.positions[order.order_id] = Position.open_position(
                    order.market_id, order.outcome, fill_amount, order.price
                )
        return True

    def get_position(self, order_id: str) -> Position | None:
        return self.positions.get(order_id)

    def resolve_market(self, market_id: str, winning_outcome: str) -> bool:
        market = self.markets.get(market_id)
        if not market:
            return False
        market.resolve(winning_outcome)
        for pos in self.positions.values():
            if pos.market_id == market_id:
                idx = market.outcomes.index(winning_outcome) if winning_outcome in market.outcomes else 0
                current_price = market.outcome_prices[idx]
                pos.close(current_price)
        return True

    @property
    def market_count(self) -> int:
        return len(self.markets)

    @property
    def order_count(self) -> int:
        return len(self.orders)

    @property
    def position_count(self) -> int:
        return len(self.positions)

    def total_volume(self) -> float:
        return sum(m.volume for m in self.markets.values())

    def active_markets(self) -> list[str]:
        return [m.market_id for m in self.markets.values() if m.active and not m.closed]

    def resolved_markets(self) -> list[str]:
        return [m.market_id for m in self.markets.values() if m.resolved]


@dataclass
class PredictionMarketAnalyzer:
    """Analysis tools for prediction markets."""
    @staticmethod
    def market_efficiency(prices: list[float]) -> float:
        if not prices:
            return 0.0
        n = len(prices)
        prob_sum = sum(prices)
        if n <= 1 or prob_sum <= 0:
            return 0.0
        deviation = abs(prob_sum - 1.0) / n
        return max(0.0, 1.0 - deviation * 10)

    @staticmethod
    def arbitrage_opportunities(markets: dict[str, PredictionMarket]) -> list[dict[str, Any]]:
        opportunities = []
        outcomes_map = defaultdict(list)
        for market in markets.values():
            for outcome, price in zip(market.outcomes, market.outcome_prices):
                outcomes_map[outcome].append((market.market_id, price))
        for outcome, price_list in outcomes_map.items():
            if len(price_list) >= 2:
                best_buy = min(price_list, key=lambda x: x[1])
                best_sell = max(price_list, key=lambda x: x[1])
                if best_sell[1] > best_buy[1]:
                    opportunities.append({
                        "outcome": outcome,
                        "buy_market": best_buy[0],
                        "buy_price": best_buy[1],
                        "sell_market": best_sell[0],
                        "sell_price": best_sell[1],
                        "profit": best_sell[1] - best_buy[1],
                    })
        return opportunities

    @staticmethod
    def consensus_price(markets: list[PredictionMarket], outcome: str) -> float:
        prices = []
        for market in markets:
            if outcome in market.outcomes:
                idx = market.outcomes.index(outcome)
                prices.append(market.outcome_prices[idx])
        if not prices:
            return 0.0
        return sum(prices) / len(prices)

    @staticmethod
    def calculate_implied_odds(price: float) -> float:
        if price <= 0:
            return float('inf')
        if price >= 1:
            return 0.0
        return (1 - price) / price

    @staticmethod
    def kelly_fraction(price: float, payout: float = 1.0) -> float:
        if price <= 0 or price >= 1:
            return 0.0
        edge = payout - price
        if edge <= 0:
            return 0.0
        return max(0.0, price * edge / payout)

    @staticmethod
    def volume_weighted_price(markets: dict[str, PredictionMarket], outcome: str) -> float:
        total_vol = 0.0
        total_price_vol = 0.0
        for market in markets.values():
            if outcome in market.outcomes:
                idx = market.outcomes.index(outcome)
                price = market.outcome_prices[idx]
                vol = market.volume
                total_vol += vol
                total_price_vol += price * vol
        if total_vol <= 0:
            return 0.0
        return total_price_vol / total_vol

    @staticmethod
    def fetch_live_prediction_odds(slug: str = "fed-interest-rate") -> dict[str, Any]:
        """Fetch live prediction market odds via Gamma API or simulated fallbacks."""
        import urllib.request
        import json
        
        try:
            url = f"https://gamma-api.polymarket.com/events?slug={slug}&limit=1"
            req = urllib.request.Request(url, headers={"User-Agent": "FRIDAY-Universe/1.0"})
            with urllib.request.urlopen(req, timeout=3.0) as resp:
                data = json.loads(resp.read().decode())
                if data and isinstance(data, list):
                    event = data[0]
                    return {
                        "engine": "polymarket_live",
                        "title": event.get("title", slug),
                        "markets": event.get("markets", []),
                        "volume": event.get("volume", 0.0),
                        "source": "gamma_api",
                    }
        except Exception:
            pass
            
        # Fallback to simulated prediction market
        return {
            "engine": "polymarket_simulated",
            "slug": slug,
            "has_real_clob": HAS_REAL_CLOB,
            "probability": 0.65,
            "outcomes": [{"name": "Yes", "price": 0.65}, {"name": "No", "price": 0.35}],
            "source": "simulated",
        }


MarketAnalyzer = PredictionMarketAnalyzer
