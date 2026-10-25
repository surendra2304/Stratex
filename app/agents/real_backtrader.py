"""
Stratex — Backtrader Engine

Python backtesting library for trading strategies. Event-driven backtesting
with strategies, indicators, analyzers, brokers, data feeds, and cerebro engine.

Source: https://github.com/mementum/backtrader
Tests: tests/test_real_batch4b.py
"""

from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any


@dataclass
class DataFeed:
    """Backtrader data feed."""
    name: str = "data0"
    data_type: str = "ohlc"
    fields: list[str] = field(default_factory=lambda: ["open", "high", "low", "close", "volume"])

    @staticmethod
    def create_ohlc(data: list[dict]) -> dict[str, list[float]]:
        return {
            "open": [d["open"] for d in data],
            "high": [d["high"] for d in data],
            "low": [d["low"] for d in data],
            "close": [d["close"] for d in data],
            "volume": [d.get("volume", 0) for d in data],
        }

    @staticmethod
    def flatten_data(data: dict[str, list[float]]) -> list[dict]:
        length = len(data.get("close", []))
        return [
            {
                "open": data["open"][i],
                "high": data["high"][i],
                "low": data["low"][i],
                "close": data["close"][i],
                "volume": data.get("volume", [0]*length)[i],
            }
            for i in range(length)
        ]

    @property
    def field_count(self) -> int:
        return len(self.fields)


@dataclass
class BacktraderIndicator:
    """Backtrader technical indicator."""
    name: str = ""
    period: int = 14

    @staticmethod
    def sma(data: list[float], period: int) -> list[float]:
        result = []
        for i in range(len(data)):
            if i < period - 1:
                result.append(float('nan'))
            else:
                result.append(sum(data[i-period+1:i+1]) / period)
        return result

    @staticmethod
    def ema(data: list[float], period: int) -> list[float]:
        if not data:
            return []
        k = 2 / (period + 1)
        result = [data[0]]
        for i in range(1, len(data)):
            result.append(data[i] * k + result[-1] * (1 - k))
        return result

    @staticmethod
    def rsi(data: list[float], period: int = 14) -> list[float]:
        if len(data) < 2:
            return []
        gains = []
        losses = []
        for i in range(1, len(data)):
            diff = data[i] - data[i-1]
            gains.append(max(diff, 0))
            losses.append(max(-diff, 0))
        result = []
        if len(gains) >= period:
            avg_gain = sum(gains[:period]) / period
            avg_loss = sum(losses[:period]) / period
            for i in range(period, len(gains)):
                avg_gain = (avg_gain * (period - 1) + gains[i]) / period
                avg_loss = (avg_loss * (period - 1) + losses[i]) / period
                if avg_loss == 0:
                    result.append(100.0)
                else:
                    rs = avg_gain / avg_loss
                    result.append(100 - 100 / (1 + rs))
        return result

    @staticmethod
    def macd(data: list[float], fast: int = 12, slow: int = 26, signal: int = 9) -> tuple[list[float], list[float], list[float]]:
        ema_fast = BacktraderIndicator.ema(data, fast)
        ema_slow = BacktraderIndicator.ema(data, slow)
        macd_line = [f - s for f, s in zip(ema_fast, ema_slow)]
        signal_line = BacktraderIndicator.ema(macd_line, signal)
        histogram = [m - s for m, s in zip(macd_line, signal_line)]
        return macd_line, signal_line, histogram

    @staticmethod
    def bollinger_bands(data: list[float], period: int = 20, deviations: float = 2.0) -> tuple[list[float], list[float]]:
        middle = BacktraderIndicator.sma(data, period)
        upper = []
        lower = []
        for i in range(len(data)):
            if i < period - 1:
                upper.append(float('nan'))
                lower.append(float('nan'))
            else:
                window = data[i-period+1:i+1]
                mean = sum(window) / period
                var = sum((p - mean) ** 2 for p in window) / period
                std = math.sqrt(var)
                upper.append(mean + deviations * std)
                lower.append(mean - deviations * std)
        return upper, lower

    @staticmethod
    def atr(high: list[float], low: list[float], close: list[float], period: int = 14) -> list[float]:
        trs = []
        for i in range(1, len(close)):
            tr = max(high[i] - low[i], abs(high[i] - close[i-1]), abs(low[i] - close[i-1]))
            trs.append(tr)
        result = []
        if len(trs) >= period:
            avg_tr = sum(trs[:period]) / period
            for i in range(period, len(trs)):
                avg_tr = (avg_tr * (period - 1) + trs[i]) / period
                result.append(avg_tr)
        return result

    @staticmethod
    def stochastic(high: list[float], low: list[float], close: list[float], period: int = 14) -> list[float]:
        result = []
        for i in range(len(close)):
            if i < period - 1:
                result.append(float('nan'))
            else:
                window_high = max(high[i-period+1:i+1])
                window_low = min(low[i-period+1:i+1])
                if window_high == window_low:
                    result.append(50.0)
                else:
                    result.append((close[i] - window_low) / (window_high - window_low) * 100)
        return result


@dataclass
class BacktraderStrategy:
    """Backtrader strategy base."""
    name: str = "DefaultStrategy"
    params: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.indicators: list[str] = []
        self.signals: list[int] = []

    @staticmethod
    def generate_signals(data: dict[str, list[float]], strategy_type: str = "sma_crossover") -> list[int]:
        closes = data.get("close", [])
        signals = []
        if strategy_type == "sma_crossover":
            short_sma = BacktraderIndicator.sma(closes, 10)
            long_sma = BacktraderIndicator.sma(closes, 30)
            for i in range(len(closes)):
                if i < 30:
                    signals.append(0)
                else:
                    if short_sma[i] > long_sma[i] and short_sma[i-1] <= long_sma[i-1]:
                        signals.append(1)
                    elif short_sma[i] < long_sma[i] and short_sma[i-1] >= long_sma[i-1]:
                        signals.append(-1)
                    else:
                        signals.append(0)
        elif strategy_type == "rsi_reversal":
            rsi_values = BacktraderIndicator.rsi(closes, 14)
            for i in range(len(closes)):
                if i < len(rsi_values):
                    if rsi_values[i] < 30:
                        signals.append(1)
                    elif rsi_values[i] > 70:
                        signals.append(-1)
                    else:
                        signals.append(0)
                else:
                    signals.append(0)
        elif strategy_type == "macd_crossover":
            macd_line, signal_line, _ = BacktraderIndicator.macd(closes)
            for i in range(len(closes)):
                if macd_line[i] > signal_line[i]:
                    signals.append(1)
                elif macd_line[i] < signal_line[i]:
                    signals.append(-1)
                else:
                    signals.append(0)
        return signals

    @property
    def strategy_name(self) -> str:
        return self.name


@dataclass
class BacktraderBroker:
    """Backtrader broker simulation."""
    cash: float = 100000.0
    commission: float = 0.001
    position: float = 0.0
    order_history: list[dict] = field(default_factory=list)

    def buy(self, price: float, size: float = 1.0) -> dict[str, Any]:
        cost = price * size
        commission_cost = cost * self.commission
        total_cost = cost + commission_cost
        if total_cost > self.cash:
            return {"executed": False, "reason": "Insufficient cash"}
        self.cash -= total_cost
        self.position += size
        order = {"type": "BUY", "price": price, "size": size, "commission": commission_cost}
        self.order_history.append(order)
        return order

    def sell(self, price: float, size: float = 1.0) -> dict[str, Any]:
        if size > self.position:
            return {"executed": False, "reason": "Insufficient position"}
        revenue = price * size
        commission_cost = revenue * self.commission
        self.cash += revenue - commission_cost
        self.position -= size
        order = {"type": "SELL", "price": price, "size": size, "commission": commission_cost}
        self.order_history.append(order)
        return order

    @property
    def portfolio_value(self) -> float:
        return self.cash + self.position * 0  # simplified, no price reference

    @property
    def position_size(self) -> float:
        return self.position

    @property
    def trade_count(self) -> int:
        return len(self.order_history)


@dataclass
class BacktraderAnalyzer:
    """Backtrader performance analyzer."""
    @staticmethod
    def returns(portfolio_values: list[float]) -> list[float]:
        returns = []
        for i in range(1, len(portfolio_values)):
            if portfolio_values[i-1] > 0:
                returns.append(portfolio_values[i] / portfolio_values[i-1] - 1)
            else:
                returns.append(0.0)
        return returns

    @staticmethod
    def sharpe(portfolio_values: list[float], risk_free: float = 0.0) -> float:
        returns = BacktraderAnalyzer.returns(portfolio_values)
        if len(returns) < 2:
            return 0.0
        excess = [r - risk_free / 252 for r in returns]
        mean = sum(excess) / len(excess)
        var = sum((r - mean) ** 2 for r in excess) / (len(excess) - 1)
        std = math.sqrt(var)
        if std == 0:
            return 0.0
        return (mean / std) * math.sqrt(252)

    @staticmethod
    def drawdown(portfolio_values: list[float]) -> float:
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
    def calmar(portfolio_values: list[float]) -> float:
        if len(portfolio_values) < 2:
            return 0.0
        total_ret = (portfolio_values[-1] - portfolio_values[0]) / portfolio_values[0] if portfolio_values[0] > 0 else 0.0
        years = len(portfolio_values) / 252
        if years <= 0:
            return 0.0
        ann_ret = (1 + total_ret) ** (1 / years) - 1
        max_dd = BacktraderAnalyzer.drawdown(portfolio_values)
        if max_dd <= 0:
            return 0.0
        return ann_ret / max_dd

    @staticmethod
    def trade_analyzer(trades: list[dict]) -> dict[str, Any]:
        if not trades:
            return {"total_trades": 0, "win_rate": 0.0, "profit_factor": 0.0, "avg_trade": 0.0}
        profits = [t.get("pnl", 0) for t in trades]
        wins = [p for p in profits if p > 0]
        losses = [p for p in profits if p <= 0]
        win_rate = len(wins) / len(trades)
        profit_factor = abs(sum(wins) / sum(losses)) if losses and sum(losses) != 0 else float('inf') if wins else 0.0
        avg_trade = sum(profits) / len(trades)
        return {
            "total_trades": len(trades),
            "win_rate": win_rate,
            "profit_factor": profit_factor,
            "avg_trade": avg_trade,
        }


@dataclass
class BacktraderCerebro:
    """Backtrader cerebro engine — unified backtesting."""
    @staticmethod
    def run(data: dict[str, list[float]], strategy_type: str = "sma_crossover",
            initial_cash: float = 100000.0, commission: float = 0.001) -> dict[str, Any]:
        closes = data.get("close", [])
        signals = BacktraderStrategy.generate_signals(data, strategy_type)
        broker = BacktraderBroker(cash=initial_cash, commission=commission)
        portfolio_values = [initial_cash]
        entry_price = 0.0
        trades = []
        position = False
        for i in range(len(closes)):
            if not position and signals[i] == 1:
                broker.buy(closes[i], size=0.1)
                entry_price = closes[i]
                position = True
            elif position and signals[i] == -1:
                broker.sell(closes[i], size=0.1)
                pnl = (closes[i] - entry_price) / entry_price
                trades.append({"entry": entry_price, "exit": closes[i], "pnl": pnl})
                position = False
            portfolio_values.append(broker.cash)
        if position:
            pnl = (closes[-1] - entry_price) / entry_price
            trades.append({"entry": entry_price, "exit": closes[-1], "pnl": pnl})
        trade_stats = BacktraderAnalyzer.trade_analyzer(trades)
        return {
            "portfolio_values": portfolio_values,
            "trades": trades,
            "trade_stats": trade_stats,
            "final_value": broker.cash,
            "sharpe": BacktraderAnalyzer.sharpe(portfolio_values),
            "drawdown": BacktraderAnalyzer.drawdown(portfolio_values),
        }

    @staticmethod
    def add_data_feed(data: dict[str, list[float]], name: str = "data0") -> DataFeed:
        return DataFeed(name=name, data_type="ohlc")

    @staticmethod
    def optimize(data: dict[str, list[float]], param_space: dict[str, list],
                 strategy_type: str = "sma_crossover") -> list[dict[str, Any]]:
        results = []
        import random
        param_names = list(param_space.keys())
        for _ in range(50):
            trial = {}
            for param in param_names:
                trial[param] = random.choice(param_space[param])
            result = BacktraderCerebro.run(data, strategy_type, initial_cash=100000.0)
            result["params"] = trial
            results.append(result)
        return results
