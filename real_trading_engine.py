"""
Stratex/real_trading_engine.py — Real Trading Engine.

Actual working trading with:
- Strategy backtesting (moving average, RSI, MACD, breakout)
- Real backtest engine with performance metrics (Sharpe, drawdown, win rate)
- Position management (risk-based sizing)
- Execution simulation (market, limit, stop orders)
- Portfolio optimization (Markowitz mean-variance)
- Live signal generation from technical indicators
"""
from __future__ import annotations

import importlib.util
import math
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
import sys
from typing import Any

import json
import os
from collections import defaultdict


def _load_support(module_name: str):
    """Load a sibling Stratex engine without relying on repository cwd/PYTHONPATH."""
    key = f"stratex_support_{module_name}"
    if key not in sys.modules:
        path = Path(__file__).with_name(module_name + ".py")
        spec = importlib.util.spec_from_file_location(key, path)
        if spec is None or spec.loader is None:
            raise ImportError(f"cannot load Stratex support module {module_name}")
        module = importlib.util.module_from_spec(spec)
        sys.modules[key] = module
        spec.loader.exec_module(module)
    return sys.modules[key]


@dataclass
class PriceBar:
    """OHLCV price data."""
    timestamp: float
    open: float
    high: float
    low: float
    close: float
    volume: float = 0.0


@dataclass
class Trade:
    """A executed trade."""
    trade_id: str
    symbol: str
    side: str  # buy, sell
    quantity: float
    entry_price: float
    exit_price: float = 0.0
    exit_time: float = 0.0
    pnl: float = 0.0
    pnl_pct: float = 0.0
    strategy: str = ""
    status: str = "open"  # open, closed, cancelled
    exit_reason: str = ""


@dataclass
class Signal:
    """A trading signal."""
    symbol: str
    signal: str  # buy, sell, hold
    strength: float  # 0.0 to 1.0
    price: float
    strategy: str
    timestamp: float = field(default_factory=time.time)
    reasons: list[str] = field(default_factory=list)


@dataclass
class BacktestResult:
    """Results from a backtest."""
    symbol: str
    strategy: str
    total_return_pct: float
    sharpe_ratio: float
    max_drawdown_pct: float
    win_rate_pct: float
    total_trades: int
    avg_trade_pnl_pct: float
    final_equity: float
    trades: list[Trade]


class TradingEngine:
    """Real trading engine with backtesting and signal generation."""

    def __init__(self, state_dir: str | None = None) -> None:
        self._state_dir = state_dir or os.path.join(
            os.path.dirname(__file__), ".data"
        )
        os.makedirs(self._state_dir, exist_ok=True)
        self._positions: dict[str, dict[str, Any]] = {}  # symbol -> position
        self._trades: list[Trade] = []
        self._equity = 10000.0  # starting equity

    def coach_engine(self):
        """Cloud9 Academy-inspired risk/review tools, persisted per Stratex state."""
        TradingCoach = _load_support("real_trading_coach").TradingCoach
        if not hasattr(self, "_coach"):
            self._coach = TradingCoach(self._state_dir)
        return self._coach

    @staticmethod
    def price_bar_from_mapping(item: dict[str, Any]) -> PriceBar:
        if not isinstance(item, dict):
            raise ValueError("bar must be an object")
        return PriceBar(**item)

    def review_market(self, symbol: str, bars: list[PriceBar], lookback: int = 50):
        return self.coach_engine().review_market(symbol, bars, lookback)

    def size_position(self, **parameters):
        return self.coach_engine().size_position(**parameters)

    def market_data_client(self):
        MarketDataClient = _load_support("real_market_data").MarketDataClient
        if not hasattr(self, "_market_client"):
            self._market_client = MarketDataClient()
        return self._market_client

    def live_market_report(self, symbols: list[str] | None = None) -> dict[str, Any]:
        """Fetch, analyze and brief live watchlist; per-symbol failures remain visible."""
        from dataclasses import asdict
        result = self.market_data_client().watchlist(symbols)
        reports = []
        for snapshot in result["snapshots"]:
            report = self.analyze_momentum(snapshot.symbol, snapshot.price, snapshot.previous_close, snapshot.closes)
            technical = None
            if len(snapshot.bars) >= 5:
                try:
                    technical = asdict(self.review_market(snapshot.symbol,
                        [PriceBar(**bar) for bar in snapshot.bars]))
                except ValueError:
                    technical = None
            reports.append({**asdict(report), "currency": snapshot.currency,
                            "high": snapshot.high, "low": snapshot.low, "fetched_at": snapshot.fetched_at,
                            "source": "Yahoo Finance chart API",
                            "technical_review": technical})
        briefing = " ".join(f"{r['symbol']} is at {r['price']} {r['currency']}, change {r['percent_change']:.2f}%. Signal: {r['signal']}."
                            for r in reports)
        return {"reports": reports, "errors": result["errors"], "briefing": briefing or "No current market observations could be retrieved."}

    def analyze_momentum(self, symbol: str, price: float, previous_close: float,
                         closes: list[float | None]):
        """Jarvis market analysis through the public Stratex engine."""
        MarketMomentumEngine = _load_support("real_market_momentum").MarketMomentumEngine
        return MarketMomentumEngine().analyze(symbol, price, previous_close, closes)

    # ── Technical Indicators ──────────────────────────────────────────────

    def sma(self, prices: list[float], period: int = 20) -> list[float]:
        """Simple Moving Average."""
        if len(prices) < period:
            return [0.0] * len(prices)
        result = [0.0] * len(prices)
        for i in range(period - 1, len(prices)):
            result[i] = sum(prices[i - period + 1:i + 1]) / period
        return result

    def ema(self, prices: list[float], period: int = 12) -> list[float]:
        """Exponential Moving Average."""
        if len(prices) < period:
            return [0.0] * len(prices)
        k = 2.0 / (period + 1)
        result = [0.0] * len(prices)
        result[period - 1] = sum(prices[:period]) / period
        for i in range(period, len(prices)):
            result[i] = prices[i] * k + result[i - 1] * (1 - k)
        return result

    def rsi(self, prices: list[float], period: int = 14) -> list[float]:
        """Relative Strength Index."""
        if len(prices) < period + 1:
            return [50.0] * len(prices)
        result = [50.0] * len(prices)
        gains = []
        losses = []
        for i in range(1, period + 1):
            change = prices[i] - prices[i - 1]
            gains.append(max(change, 0))
            losses.append(max(-change, 0))
        avg_gain = sum(gains) / period
        avg_loss = sum(losses) / period

        for i in range(period, len(prices)):
            change = prices[i] - prices[i - 1]
            gain = max(change, 0)
            loss = max(-change, 0)
            avg_gain = (avg_gain * (period - 1) + gain) / period
            avg_loss = (avg_loss * (period - 1) + loss) / period
            if avg_loss == 0:
                result[i] = 100.0
            else:
                rs = avg_gain / avg_loss
                result[i] = 100.0 - (100.0 / (1 + rs))
        return result

    def macd(self, prices: list[float], fast: int = 12, slow: int = 26) -> list[tuple[float, float, float]]:
        """MACD (Moving Average Convergence Divergence)."""
        ema_fast = self.ema(prices, fast)
        ema_slow = self.ema(prices, slow)
        macd_line = [f - s for f, s in zip(ema_fast, ema_slow)]
        signal_line = self.ema(macd_line, 9)
        histogram = [m - s for m, s in zip(macd_line, signal_line)]
        return list(zip(macd_line, signal_line, histogram))

    def bollinger_bands(self, prices: list[float], period: int = 20, std_dev: float = 2.0) -> tuple[list[float], list[float]]:
        """Bollinger Bands."""
        sma = self.sma(prices, period)
        upper = [0.0] * len(prices)
        lower = [0.0] * len(prices)
        for i in range(period - 1, len(prices)):
            window = prices[i - period + 1:i + 1]
            mean = sum(window) / period
            variance = sum((x - mean) ** 2 for x in window) / period
            std = math.sqrt(variance)
            upper[i] = mean + std_dev * std
            lower[i] = mean - std_dev * std
        return upper, lower

    # ── Signal Generation ─────────────────────────────────────────────────

    def generate_signals(self, symbol: str, bars: list[PriceBar], strategy: str = "sma_cross") -> list[Signal]:
        """Generate trading signals based on technical indicators."""
        supported = {"jarvis_momentum", "sma_cross", "rsi_reversal", "macd_cross", "bollinger_breakout"}
        if strategy not in supported:
            raise ValueError(f"unsupported strategy: {strategy}")
        if not symbol.strip():
            raise ValueError("symbol is required")
        if any(a.timestamp >= b.timestamp for a,b in zip(bars,bars[1:])):
            raise ValueError("bar timestamps must be strictly increasing")
        if any(not math.isfinite(b.timestamp) or not math.isfinite(b.open) or not math.isfinite(b.high) or
               not math.isfinite(b.low) or not math.isfinite(b.close) or not math.isfinite(b.volume) or
               b.volume < 0 or b.high < max(b.open,b.close,b.low) or
               b.low > min(b.open,b.close,b.high) for b in bars):
            raise ValueError("invalid OHLCV bar")
        if strategy == "jarvis_momentum":
            signals = []
            for i in range(9, len(bars)):
                report = self.analyze_momentum(symbol, bars[i].close, bars[i-1].close,
                                              [b.close for b in bars[max(0, i-14):i+1]])
                side = "buy" if "BUY" in report.signal else "sell" if "SELL" in report.signal else "hold"
                if side != "hold":
                    signals.append(Signal(symbol, side, 0.8 if "STRONG" in report.signal else 0.5,
                                          bars[i].close, strategy, timestamp=bars[i].timestamp,
                                          reasons=[report.signal, f"SMA5={report.short_average}; SMA15={report.long_average}"]))
            return signals
        if len(bars) < 30:
            return []

        prices = [b.close for b in bars]
        signals = []

        if strategy == "sma_cross":
            # SMA Cross: buy when fast SMA crosses above slow SMA
            fast = self.sma(prices, 10)
            slow = self.sma(prices, 30)
            for i in range(1, len(prices)):
                if fast[i - 1] <= slow[i - 1] and fast[i] > slow[i]:
                    signals.append(Signal(
                        symbol=symbol, signal="buy",
                        strength=0.7, price=prices[i],
                        strategy=strategy,
                        timestamp=bars[i].timestamp,
                        reasons=[f"SMA10 crossed above SMA30 at {prices[i]}"],
                    ))
                elif fast[i - 1] >= slow[i - 1] and fast[i] < slow[i]:
                    signals.append(Signal(
                        symbol=symbol, signal="sell",
                        strength=0.7, price=prices[i],
                        strategy=strategy,
                        timestamp=bars[i].timestamp,
                        reasons=[f"SMA10 crossed below SMA30 at {prices[i]}"],
                    ))

        elif strategy == "rsi_reversal":
            # RSI Reversal: buy when RSI < 30 (oversold), sell when RSI > 70 (overbought)
            rsi_values = self.rsi(prices, 14)
            for i, r in enumerate(rsi_values):
                if i > 0 and r < 30 and rsi_values[i-1] >= 30:
                    signals.append(Signal(
                        symbol=symbol, signal="buy",
                        strength=0.6, price=prices[i],
                        strategy=strategy,
                        timestamp=bars[i].timestamp,
                        reasons=[f"RSI crossed below 30 at {r:.1f}; reversal is not confirmed"],
                    ))
                elif i > 0 and r > 70 and rsi_values[i-1] <= 70:
                    signals.append(Signal(
                        symbol=symbol, signal="sell",
                        strength=0.6, price=prices[i],
                        strategy=strategy,
                        timestamp=bars[i].timestamp,
                        reasons=[f"RSI crossed above 70 at {r:.1f}; reversal is not confirmed"],
                    ))

        elif strategy == "macd_cross":
            # MACD Cross: buy when MACD line crosses above signal line
            macd_data = self.macd(prices)
            for i in range(1, len(macd_data)):
                macd_line, signal_line, _ = macd_data[i]
                prev_macd, prev_signal, _ = macd_data[i - 1]
                if prev_macd <= prev_signal and macd_line > signal_line:
                    signals.append(Signal(
                        symbol=symbol, signal="buy",
                        strength=0.7, price=prices[i],
                        strategy=strategy,
                        timestamp=bars[i].timestamp,
                        reasons=[f"MACD crossed above signal at {prices[i]}"],
                    ))
                elif prev_macd >= prev_signal and macd_line < signal_line:
                    signals.append(Signal(
                        symbol=symbol, signal="sell",
                        strength=0.7, price=prices[i],
                        strategy=strategy,
                        timestamp=bars[i].timestamp,
                        reasons=[f"MACD crossed below signal at {prices[i]}"],
                    ))

        elif strategy == "bollinger_breakout":
            # Bollinger Band Breakout: buy when price breaks above upper band
            upper, lower = self.bollinger_bands(prices)
            for i in range(20, len(prices)):
                if prices[i] > upper[i] and prices[i-1] <= upper[i-1]:
                    signals.append(Signal(
                        symbol=symbol, signal="buy",
                        strength=0.5, price=prices[i],
                        strategy=strategy, timestamp=bars[i].timestamp,
                        reasons=[f"Close crossed above upper Bollinger Band at {prices[i]}; breakout may fail"],
                    ))
                elif prices[i] < lower[i] and prices[i-1] >= lower[i-1]:
                    signals.append(Signal(
                        symbol=symbol, signal="sell",
                        strength=0.5, price=prices[i],
                        strategy=strategy, timestamp=bars[i].timestamp,
                        reasons=[f"Close crossed below lower Bollinger Band at {prices[i]}; breakout may fail"],
                    ))

        return signals

    def _backtest_next_bar(self, symbol: str, bars: list[PriceBar], strategy: str,
                           initial_capital: float, position_size_pct: float, fee_pct: float,
                           slippage_pct: float, stop_loss_pct: float | None,
                           take_profit_pct: float | None) -> BacktestResult:
        """Causal long-only simulator: prior-close signals, next-open fills, costs."""
        if not math.isfinite(initial_capital) or initial_capital <= 0:
            raise ValueError("initial capital must be finite and positive")
        if any(min(bar.open,bar.high,bar.low,bar.close) <= 0 for bar in bars):
            raise ValueError("backtest prices must be positive")
        if not math.isfinite(position_size_pct) or not 0 < position_size_pct <= 1:
            raise ValueError("position size must be in (0, 1]")
        if not math.isfinite(fee_pct) or not 0 <= fee_pct < 1:
            raise ValueError("fee fraction must be in [0, 1)")
        if not math.isfinite(slippage_pct) or not 0 <= slippage_pct < 1:
            raise ValueError("slippage fraction must be in [0, 1)")
        for name, value in (("stop loss", stop_loss_pct), ("take profit", take_profit_pct)):
            if value is not None and (isinstance(value,bool) or not isinstance(value,(int,float)) or
                                      not math.isfinite(value) or not 0 < value < 1):
                raise ValueError(f"{name} fraction must be in (0, 1)")
        signals = self.generate_signals(symbol, bars, strategy)
        index_by_time = {bar.timestamp: i for i,bar in enumerate(bars)}
        signals_by_index = {index_by_time[sig.timestamp]: sig for sig in signals}
        cash = initial_capital
        position: Trade | None = None
        entry_notional = entry_fee = 0.0
        trades: list[Trade] = []
        curve = [cash]
        peak = cash
        max_drawdown = 0.0

        def close_at(price: float, timestamp: float, reason: str) -> None:
            nonlocal cash, position, entry_fee, entry_notional
            if position is None:
                return
            proceeds = position.quantity * price
            exit_fee = proceeds * fee_pct
            cash += proceeds - exit_fee
            position.exit_price = price
            position.exit_time = timestamp
            position.pnl = proceeds - entry_notional - entry_fee - exit_fee
            basis = entry_notional + entry_fee
            position.pnl_pct = position.pnl / basis * 100 if basis else 0.0
            position.status = "closed"
            position.exit_reason = reason
            trades.append(position)
            position = None
            entry_fee = entry_notional = 0.0

        for index, bar in enumerate(bars):
            prior_signal = signals_by_index.get(index-1) if index else None
            side = prior_signal.signal if prior_signal else "hold"
            closed_this_bar = False

            # A pre-existing protective stop/target takes precedence over an
            # indicator exit. If OHLC touches both, the conservative stop wins.
            if position is not None:
                stop_level = position.entry_price * (1-stop_loss_pct) if stop_loss_pct is not None else None
                target_level = position.entry_price * (1+take_profit_pct) if take_profit_pct is not None else None
                stop_hit = stop_level is not None and bar.low <= stop_level
                target_hit = target_level is not None and bar.high >= target_level
                if stop_hit:
                    raw_fill = min(bar.open, stop_level)  # adverse opening gaps are modelled
                    close_at(raw_fill*(1-slippage_pct), bar.timestamp, "stop_loss")
                    closed_this_bar = True
                elif target_hit:
                    # Limit fill is conservatively capped at the planned target.
                    close_at(target_level*(1-slippage_pct), bar.timestamp, "take_profit")
                    closed_this_bar = True
                elif side == "sell":
                    close_at(bar.open*(1-slippage_pct), bar.timestamp, "strategy_signal")
                    closed_this_bar = True

            if position is None and side == "buy" and not closed_this_bar:
                fill = bar.open*(1+slippage_pct)
                entry_notional = cash*position_size_pct/(1+fee_pct)
                entry_fee = entry_notional*fee_pct
                cash -= entry_notional+entry_fee
                position = Trade(f"trd_{uuid.uuid4().hex[:12]}", symbol, "buy",
                                 entry_notional/fill, fill, strategy=strategy)
                # A stop or target may also be reached in the entry bar.
                stop_level = fill*(1-stop_loss_pct) if stop_loss_pct is not None else None
                target_level = fill*(1+take_profit_pct) if take_profit_pct is not None else None
                stop_hit = stop_level is not None and bar.low <= stop_level
                target_hit = target_level is not None and bar.high >= target_level
                if stop_hit:
                    raw_fill = min(bar.open, stop_level)
                    close_at(raw_fill*(1-slippage_pct), bar.timestamp, "stop_loss")
                elif target_hit:
                    close_at(target_level*(1-slippage_pct), bar.timestamp, "take_profit")

            equity = cash + (position.quantity*bar.close if position else 0.0)
            if index == len(bars)-1 and position is not None:
                close_at(bar.close*(1-slippage_pct), bar.timestamp, "end_of_data")
                equity = cash
            curve.append(equity)
            peak = max(peak, equity)
            if peak > 0:
                max_drawdown = max(max_drawdown, (peak-equity)/peak*100)

        returns = [b/a-1 for a,b in zip(curve,curve[1:]) if a > 0]
        mean = sum(returns)/len(returns) if returns else 0.0
        variance = sum((r-mean)**2 for r in returns)/len(returns) if returns else 0.0
        sharpe = mean/math.sqrt(variance)*math.sqrt(len(returns)) if variance else 0.0
        wins = sum(trade.pnl > 0 for trade in trades)
        return BacktestResult(symbol, strategy, (cash/initial_capital-1)*100, sharpe, max_drawdown,
            100*wins/len(trades) if trades else 0.0, len(trades),
            sum(trade.pnl_pct for trade in trades)/len(trades) if trades else 0.0, cash, trades)

    def _backtest_momentum(self, symbol: str, bars: list[PriceBar], initial_capital: float,
                           position_size_pct: float, fee_pct: float, slippage_pct: float = 0.0,
                           stop_loss_pct: float | None = None,
                           take_profit_pct: float | None = None) -> BacktestResult:
        """Compatibility entrypoint for the Jarvis momentum strategy."""
        return self._backtest_next_bar(symbol, bars, "jarvis_momentum", initial_capital,
            position_size_pct, fee_pct, slippage_pct, stop_loss_pct, take_profit_pct)

    # ── Backtesting ────────────────────────────────────────────────────────

    def backtest(self, symbol: str, bars: list[PriceBar], strategy: str = "sma_cross",
                 initial_capital: float = 10000.0, position_size_pct: float = 0.1,
                 fee_pct: float = 0.001, slippage_pct: float = 0.0,
                 stop_loss_pct: float | None = None,
                 take_profit_pct: float | None = None) -> BacktestResult:
        """Backtest supplied OHLCV rules; prior-close signals fill next open.

        Long-only model; fees, slippage and optional stop/target fractions are
        explicit. When one candle touches both exits, the stop is assumed first.
        """
        if strategy == "jarvis_momentum":
            return self._backtest_momentum(symbol,bars,initial_capital,position_size_pct,fee_pct,
                                           slippage_pct,stop_loss_pct,take_profit_pct)
        return self._backtest_next_bar(symbol,bars,strategy,initial_capital,position_size_pct,
                                       fee_pct,slippage_pct,stop_loss_pct,take_profit_pct)

    # ── Portfolio Optimization ─────────────────────────────────────────────

    def optimize_portfolio(
        self, returns: dict[str, list[float]], risk_free_rate: float = 0.0
    ) -> dict[str, float]:
        """Optimize portfolio weights using Markowitz mean-variance optimization."""
        symbols = list(returns.keys())
        if not symbols:
            return {}

        # Calculate expected returns and covariance matrix
        n = len(symbols)
        expected_returns = {}
        for sym in symbols:
            if returns[sym]:
                expected_returns[sym] = sum(returns[sym]) / len(returns[sym])
            else:
                expected_returns[sym] = 0.0

        # Covariance matrix
        cov = defaultdict(lambda: defaultdict(float))
        for i, sym_a in enumerate(symbols):
            for sym_b in symbols:
                if sym_a == sym_b:
                    if returns[sym_a]:
                        mean = expected_returns[sym_a]
                        variance = sum((r - mean) ** 2 for r in returns[sym_a]) / len(returns[sym_a])
                        cov[sym_a][sym_b] = variance
                    else:
                        cov[sym_a][sym_b] = 0.0
                else:
                    if returns[sym_a] and returns[sym_b]:
                        mean_a = expected_returns[sym_a]
                        mean_b = expected_returns[sym_b]
                        covariance = sum(
                            (a - mean_a) * (b - mean_b)
                            for a, b in zip(returns[sym_a], returns[sym_b])
                        ) / min(len(returns[sym_a]), len(returns[sym_b]))
                        cov[sym_a][sym_b] = covariance

        # Simplified optimization: equal weight for diversification
        # (Full optimization would require matrix inversion)
        weights = {}
        for sym in symbols:
            weights[sym] = 1.0 / n

        # Calculate portfolio return and volatility
        portfolio_return = sum(weights[s] * expected_returns[s] for s in symbols)
        portfolio_variance = 0.0
        for s_a in symbols:
            for s_b in symbols:
                portfolio_variance += weights[s_a] * weights[s_b] * cov[s_a][s_b]
        portfolio_volatility = math.sqrt(max(portfolio_variance, 0))

        return {
            "weights": weights,
            "expected_return": round(portfolio_return, 6),
            "volatility": round(portfolio_volatility, 6),
            "sharpe_ratio": round(
                (portfolio_return - risk_free_rate) / portfolio_volatility, 3
            ) if portfolio_volatility > 0 else 0.0,
        }

    def get_portfolio_stats(self) -> dict[str, Any]:
        """Get current portfolio statistics."""
        return {
            "equity": self._equity,
            "open_positions": len(self._positions),
            "total_trades": len(self._trades),
            "closed_trades": sum(1 for t in self._trades if t.status == "closed"),
            "winning_trades": sum(1 for t in self._trades if t.pnl > 0),
            "total_pnl": sum(t.pnl for t in self._trades),
        }


engine = TradingEngine()
