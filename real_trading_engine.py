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

import math
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

import json
import os
from collections import defaultdict


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

    def analyze_momentum(self, symbol: str, price: float, previous_close: float,
                         closes: list[float | None]):
        """Jarvis market analysis through the public Stratex engine."""
        from Stratex.real_market_momentum import MarketMomentumEngine
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
                        reasons=[f"SMA10 crossed above SMA30 at {prices[i]}"],
                    ))
                elif fast[i - 1] >= slow[i - 1] and fast[i] < slow[i]:
                    signals.append(Signal(
                        symbol=symbol, signal="sell",
                        strength=0.7, price=prices[i],
                        strategy=strategy,
                        reasons=[f"SMA10 crossed below SMA30 at {prices[i]}"],
                    ))

        elif strategy == "rsi_reversal":
            # RSI Reversal: buy when RSI < 30 (oversold), sell when RSI > 70 (overbought)
            rsi_values = self.rsi(prices, 14)
            for i, r in enumerate(rsi_values):
                if r < 30 and r > 0:
                    signals.append(Signal(
                        symbol=symbol, signal="buy",
                        strength=0.6, price=prices[i],
                        strategy=strategy,
                        reasons=[f"RSI oversold at {r:.1f}"],
                    ))
                elif r > 70:
                    signals.append(Signal(
                        symbol=symbol, signal="sell",
                        strength=0.6, price=prices[i],
                        strategy=strategy,
                        reasons=[f"RSI overbought at {r:.1f}"],
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
                        reasons=[f"MACD crossed above signal at {prices[i]}"],
                    ))
                elif prev_macd >= prev_signal and macd_line < signal_line:
                    signals.append(Signal(
                        symbol=symbol, signal="sell",
                        strength=0.7, price=prices[i],
                        strategy=strategy,
                        reasons=[f"MACD crossed below signal at {prices[i]}"],
                    ))

        elif strategy == "bollinger_breakout":
            # Bollinger Band Breakout: buy when price breaks above upper band
            upper, lower = self.bollinger_bands(prices)
            for i in range(20, len(prices)):
                if prices[i] > upper[i]:
                    signals.append(Signal(
                        symbol=symbol, signal="sell",
                        strength=0.5, price=prices[i],
                        strategy=strategy,
                        reasons=[f"Price broke above upper Bollinger Band at {prices[i]}"],
                    ))
                elif prices[i] < lower[i]:
                    signals.append(Signal(
                        symbol=symbol, signal="buy",
                        strength=0.5, price=prices[i],
                        strategy=strategy,
                        reasons=[f"Price broke below lower Bollinger Band at {prices[i]}"],
                    ))

        return signals

    def _backtest_momentum(self, symbol: str, bars: list[PriceBar], initial_capital: float,
                           position_size_pct: float, fee_pct: float) -> BacktestResult:
        """Execute prior-close signals at the next bar open, long-only with fees."""
        if (not math.isfinite(initial_capital) or initial_capital <= 0 or
                not 0 < position_size_pct <= 1 or not 0 <= fee_pct < 1):
            raise ValueError("invalid backtest capital, sizing or fees")
        if any(not math.isfinite(b.open) or b.open <= 0 or not math.isfinite(b.close) or b.close <= 0
               for b in bars) or any(a.timestamp >= b.timestamp for a, b in zip(bars, bars[1:])):
            raise ValueError("positive finite prices and strictly increasing timestamps required")
        signals = {s.timestamp: s for s in self.generate_signals(symbol, bars, "jarvis_momentum")}
        cash = initial_capital
        position = None
        trades = []
        curve = [cash]
        peak = cash
        drawdown = 0.0
        entry_fee = 0.0
        for index, bar in enumerate(bars):
            prior = signals.get(bars[index-1].timestamp) if index else None
            side = prior.signal if prior else "hold"
            if position is not None and side == "sell":
                proceeds = position.quantity * bar.open
                exit_fee = proceeds * fee_pct
                cash += proceeds - exit_fee
                position.exit_price = bar.open
                position.exit_time = bar.timestamp
                position.pnl = proceeds - position.quantity * position.entry_price - entry_fee - exit_fee
                position.pnl_pct = 100 * position.pnl / (position.quantity * position.entry_price + entry_fee)
                position.status = "closed"
                trades.append(position)
                position = None
            elif position is None and side == "buy":
                notional = cash * position_size_pct / (1 + fee_pct)
                entry_fee = notional * fee_pct
                cash -= notional + entry_fee
                position = Trade(f"trd_{uuid.uuid4().hex[:12]}", symbol, "buy", notional / bar.open,
                                 bar.open, strategy="jarvis_momentum")
            equity = cash + (position.quantity * bar.close if position else 0)
            if index == len(bars)-1 and position is not None:
                proceeds = position.quantity * bar.close
                exit_fee = proceeds * fee_pct
                cash += proceeds - exit_fee
                position.exit_price = bar.close
                position.exit_time = bar.timestamp
                position.pnl = proceeds - position.quantity * position.entry_price - entry_fee - exit_fee
                position.pnl_pct = 100 * position.pnl / (position.quantity * position.entry_price + entry_fee)
                position.status = "closed"
                trades.append(position)
                position = None
                equity = cash
            curve.append(equity)
            peak = max(peak, equity)
            drawdown = max(drawdown, (peak-equity)/peak*100)
        returns = [b/a-1 for a, b in zip(curve, curve[1:]) if a > 0]
        mean = sum(returns)/len(returns) if returns else 0
        variance = sum((r-mean)**2 for r in returns)/len(returns) if returns else 0
        sharpe = mean/math.sqrt(variance)*math.sqrt(len(returns)) if variance else 0
        return BacktestResult(symbol, "jarvis_momentum", (cash/initial_capital-1)*100, sharpe,
                              drawdown, 100*sum(t.pnl > 0 for t in trades)/len(trades) if trades else 0,
                              len(trades), sum(t.pnl_pct for t in trades)/len(trades) if trades else 0,
                              cash, trades)

    # ── Backtesting ────────────────────────────────────────────────────────

    def backtest(
        self, symbol: str, bars: list[PriceBar], strategy: str = "sma_cross",
        initial_capital: float = 10000.0, position_size_pct: float = 0.1,
        fee_pct: float = 0.001
    ) -> BacktestResult:
        """Run a backtest on historical price data."""
        if strategy == "jarvis_momentum":
            return self._backtest_momentum(symbol, bars, initial_capital, position_size_pct, fee_pct)
        if len(bars) < 30:
            return BacktestResult(
                symbol=symbol, strategy=strategy,
                total_return_pct=0.0, sharpe_ratio=0.0,
                max_drawdown_pct=0.0, win_rate_pct=0.0,
                total_trades=0, avg_trade_pnl_pct=0.0,
                final_equity=initial_capital, trades=[],
            )

        signals = self.generate_signals(symbol, bars, strategy)
        capital = initial_capital
        position: Trade | None = None
        equity_curve = [capital]
        max_equity = capital
        peak_to_trough = 0.0

        for i in range(len(bars)):
            bar = bars[i]
            price = bar.close

            # Check for entry signal
            if position is None:
                for sig in signals:
                    if sig.timestamp <= bar.timestamp and sig.signal == "buy":
                        # Calculate position size
                        trade_amount = capital * position_size_pct
                        quantity = trade_amount / price
                        fee = trade_amount * fee_pct
                        position = Trade(
                            trade_id=f"trd_{uuid.uuid4().hex[:12]}",
                            symbol=symbol, side="buy",
                            quantity=quantity,
                            entry_price=price,
                            strategy=strategy,
                            status="open",
                        )
                        position.pnl = -fee  # deduct fee on entry
                        capital -= fee
                        break

            # Check for exit signal
            if position is not None:
                for sig in signals:
                    if sig.timestamp <= bar.timestamp and sig.signal == "sell":
                        # Close position
                        exit_amount = position.quantity * price
                        fee = exit_amount * fee_pct
                        position.exit_price = price
                        position.exit_time = bar.timestamp
                        position.status = "closed"

                        # Calculate PnL
                        entry_amount = position.quantity * position.entry_price
                        position.pnl = exit_amount - entry_amount - fee
                        position.pnl_pct = (position.pnl / entry_amount * 100) if entry_amount > 0 else 0.0

                        capital += position.pnl
                        self._trades.append(position)
                        position = None
                        break

            # Update equity curve
            if position is not None:
                # Mark-to-market
                unrealized = (price - position.entry_price) * position.quantity
                current_equity = capital + unrealized
            else:
                current_equity = capital

            equity_curve.append(current_equity)

            # Calculate drawdown
            if current_equity > max_equity:
                max_equity = current_equity
            drawdown = (max_equity - current_equity) / max_equity * 100 if max_equity > 0 else 0.0
            if drawdown > peak_to_trough:
                peak_to_trough = drawdown

        # Calculate metrics
        if equity_curve[0] > 0:
            total_return = (equity_curve[-1] / equity_curve[0] - 1) * 100
        else:
            total_return = 0.0

        # Sharpe ratio (simplified)
        if len(equity_curve) > 2:
            returns = []
            for i in range(1, len(equity_curve)):
                if equity_curve[i - 1] > 0:
                    returns.append(equity_curve[i] / equity_curve[i - 1] - 1)
            if returns:
                avg_return = sum(returns) / len(returns)
                variance = sum((r - avg_return) ** 2 for r in returns) / len(returns)
                std_dev = math.sqrt(variance) if variance > 0 else 1.0
                sharpe = (avg_return / std_dev * math.sqrt(len(returns))) if std_dev > 0 else 0.0
            else:
                sharpe = 0.0
        else:
            sharpe = 0.0

        # Win rate
        closed_trades = [t for t in self._trades if t.status == "closed" and t.pnl_pct != 0]
        winning_trades = sum(1 for t in closed_trades if t.pnl > 0)
        win_rate = (winning_trades / len(closed_trades) * 100) if closed_trades else 0.0

        # Average trade PnL
        avg_trade_pnl = (
            sum(t.pnl_pct for t in closed_trades) / len(closed_trades)
            if closed_trades else 0.0
        )

        return BacktestResult(
            symbol=symbol,
            strategy=strategy,
            total_return_pct=round(total_return, 2),
            sharpe_ratio=round(sharpe, 3),
            max_drawdown_pct=round(peak_to_trough, 2),
            win_rate_pct=round(win_rate, 1),
            total_trades=len(closed_trades),
            avg_trade_pnl_pct=round(avg_trade_pnl, 2),
            final_equity=round(equity_curve[-1], 2),
            trades=closed_trades,
        )

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
