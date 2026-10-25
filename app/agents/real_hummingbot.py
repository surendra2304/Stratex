"""
Stratex — Hummingbot Engine

Open source software for high-frequency crypto trading bots. Market making,
cross-exchange arbitrage, connectors, controllers, scheduler, paper trading.

Source: https://github.com/hummingbot/hummingbot
Tests: tests/test_real_batch4b.py
"""

from __future__ import annotations

import math
import time
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any


@dataclass
class ConnectorConfig:
    """Exchange connector configuration."""
    name: str = "binance"
    trading_pair: str = "BTC-USDT"
    base_asset: str = "BTC"
    quote_asset: str = "USDT"
    min_order_size: float = 0.001
    max_order_size: float = 100.0
    order_levels: int = 5
    order_level_amount: float = 0.01
    order_level_spread: float = 0.0001
    is_active: bool = True

    @property
    def trading_pair_symbol(self) -> str:
        return self.base_asset + self.quote_asset

    def spread_at_level(self, level: int) -> float:
        return self.order_level_spread * (level + 1)

    def size_at_level(self, level: int) -> float:
        return self.order_level_amount * (level + 1)

    @property
    def total_order_size(self) -> float:
        return sum(self.size_at_level(i) for i in range(self.order_levels))


@dataclass
class MarketMakingStrategy:
    """Market making strategy."""
    @staticmethod
    def calculate_bid_ask(mid_price: float, spread: float, level: int = 0) -> tuple[float, float]:
        half_spread = spread / 2
        bid = mid_price * (1 - half_spread * (level + 1))
        ask = mid_price * (1 + half_spread * (level + 1))
        return bid, ask

    @staticmethod
    def inventory_imbalance(bids: list[tuple[float, float]], asks: list[tuple[float, float]]) -> float:
        bid_vol = sum(q for _, q in bids)
        ask_vol = sum(q for _, q in asks)
        total = bid_vol + ask_vol
        if total == 0:
            return 0.0
        return (bid_vol - ask_vol) / total

    @staticmethod
    def adjust_spread(mid_price: float, inventory: float, base_spread: float = 0.0001, max_spread: float = 0.005) -> float:
        adjusted = base_spread * (1 + abs(inventory) * 2)
        return min(adjusted, max_spread)

    @staticmethod
    def hedge_ratio(base_inventory: float, quote_inventory: float) -> float:
        if base_inventory == 0:
            return 0.0
        return -quote_inventory / base_inventory

    @staticmethod
    def pnl_calculation(entry_price: float, exit_price: float, quantity: float, is_buy: bool) -> float:
        if is_buy:
            return (exit_price - entry_price) * quantity
        else:
            return (entry_price - exit_price) * quantity

    @staticmethod
    def calculate_spread_pnl(bid_fills: list[float], ask_fills: list[float], mid_price: float) -> float:
        total_pnl = 0.0
        for bid_fill in bid_fills:
            total_pnl += (mid_price - bid_fill)
        for ask_fill in ask_fills:
            total_pnl += (ask_fill - mid_price)
        return total_pnl


@dataclass
class CrossExchangeArbitrage:
    """Cross-exchange arbitrage detector."""
    @staticmethod
    def find_arbitrage(prices: dict[str, float]) -> list[dict[str, Any]]:
        symbols = list(prices.keys())
        opportunities = []
        for i in range(len(symbols)):
            for j in range(i + 1, len(symbols)):
                ex1, ex2 = symbols[i], symbols[j]
                p1 = prices[ex1]
                p2 = prices[ex2]
                if p1 > 0 and p2 > 0:
                    if p1 > p2:
                        profit_pct = (p1 - p2) / p2
                        if profit_pct > 0.001:
                            opportunities.append({
                                "buy_exchange": ex2,
                                "sell_exchange": ex1,
                                "buy_price": p2,
                                "sell_price": p1,
                                "profit_pct": profit_pct,
                                "profit_abs": p1 - p2,
                            })
                    elif p2 > p1:
                        profit_pct = (p2 - p1) / p1
                        if profit_pct > 0.001:
                            opportunities.append({
                                "buy_exchange": ex1,
                                "sell_exchange": ex2,
                                "buy_price": p1,
                                "sell_price": p2,
                                "profit_pct": profit_pct,
                                "profit_abs": p2 - p1,
                            })
        return opportunities

    @staticmethod
    def calculate_fee_adjusted_profit(buy_price: float, sell_price: float, quantity: float,
                                       buy_fee: float = 0.001, sell_fee: float = 0.001) -> dict[str, Any]:
        gross_profit = (sell_price - buy_price) * quantity
        buy_cost = buy_price * quantity
        sell_revenue = sell_price * quantity
        fees = buy_cost * buy_fee + sell_revenue * sell_fee
        net_profit = gross_profit - fees
        return {
            "gross_profit": gross_profit,
            "fees": fees,
            "net_profit": net_profit,
            "profit_pct": net_profit / buy_cost if buy_cost > 0 else 0.0,
        }

    @staticmethod
    def check_transfer_time(exchanges: list[str], transfer_times: dict[str, float]) -> dict[str, float]:
        return {ex: transfer_times.get(ex, 60.0) for ex in exchanges}


@dataclass
class SchedulerController:
    """Hummingbot scheduler/controller."""
    @staticmethod
    def schedule_trade(interval: float, total_runs: int = -1) -> list[float]:
        if total_runs == -1:
            return [interval * (i + 1) for i in range(100)]
        return [interval * (i + 1) for i in range(total_runs)]

    @staticmethod
    def parse_schedule(schedule_str: str) -> float:
        s = schedule_str.strip()
        parts = s.split()
        if len(parts) >= 2:
            value = float(parts[0])
            unit = parts[1].lower()
            multipliers = {"s": 1, "m": 60, "h": 3600, "d": 86400}
            return value * multipliers.get(unit, 1)
        # handle concatenated format like "5m", "1h"
        import re
        match = re.match(r'^(\d+(?:\.\d+)?)\s*([smhd])$', s, re.IGNORECASE)
        if match:
            value = float(match.group(1))
            unit = match.group(2).lower()
            multipliers = {"s": 1, "m": 60, "h": 3600, "d": 86400}
            return value * multipliers.get(unit, 1)
        return float(s)

    @staticmethod
    def next_run_time(current_time: float, interval: float) -> float:
        return ((current_time // interval) + 1) * interval

    @staticmethod
    def should_run(current_time: float, last_run: float, interval: float) -> bool:
        return (current_time - last_run) >= interval


@dataclass
class PaperTradingEngine:
    """Paper trading engine for Hummingbot."""
    cash: float = 100000.0
    positions: dict[str, float] = field(default_factory=dict)
    trade_history: list[dict] = field(default_factory=list)

    def place_order(self, symbol: str, side: str, quantity: float, price: float) -> dict[str, Any]:
        if side == "buy":
            cost = quantity * price
            if cost > self.cash:
                return {"executed": False, "reason": "Insufficient cash"}
            self.cash -= cost
            self.positions[symbol] = self.positions.get(symbol, 0) + quantity
        elif side == "sell":
            if self.positions.get(symbol, 0) < quantity:
                return {"executed": False, "reason": "Insufficient position"}
            self.cash += quantity * price
            self.positions[symbol] = self.positions.get(symbol, 0) - quantity
        order = {"symbol": symbol, "side": side, "quantity": quantity, "price": price, "timestamp": len(self.trade_history)}
        self.trade_history.append(order)
        return order

    def portfolio_value(self, prices: dict[str, float]) -> float:
        value = self.cash
        for symbol, quantity in self.positions.items():
            price = prices.get(symbol, 0)
            value += quantity * price
        return value

    def pnl(self, symbol: str, entry_price: float, current_price: float) -> float:
        quantity = self.positions.get(symbol, 0)
        return (current_price - entry_price) * quantity

    @property
    def trade_count(self) -> int:
        return len(self.trade_history)

    @property
    def position_count(self) -> int:
        return len([s for s, q in self.positions.items() if q > 0])


@dataclass
class HummingbotManager:
    """Unified Hummingbot manager."""
    connector: ConnectorConfig = field(default_factory=ConnectorConfig)
    strategy: MarketMakingStrategy = field(default_factory=MarketMakingStrategy)
    paper_engine: PaperTradingEngine = field(default_factory=PaperTradingEngine)

    def generate_orders(self, mid_price: float) -> dict[str, list[tuple[float, float]]]:
        orders = {"bids": [], "asks": []}
        for level in range(self.connector.order_levels):
            bid, ask = MarketMakingStrategy.calculate_bid_ask(mid_price, self.connector.order_level_spread, level)
            bid_size = self.connector.size_at_level(level)
            ask_size = self.connector.size_at_level(level)
            orders["bids"].append((bid, bid_size))
            orders["asks"].append((ask, ask_size))
        return orders

    def detect_arbitrage(self, prices: dict[str, float]) -> list[dict[str, Any]]:
        return CrossExchangeArbitrage.find_arbitrage(prices)

    def run_paper_trade(self, symbol: str, side: str, quantity: float, price: float) -> dict[str, Any]:
        return self.paper_engine.place_order(symbol, side, quantity, price)

    def get_portfolio_value(self, prices: dict[str, float]) -> float:
        return self.paper_engine.portfolio_value(prices)

    @property
    def active_orders(self) -> int:
        return self.connector.order_levels * 2

    @property
    def connector_name(self) -> str:
        return self.connector.name
