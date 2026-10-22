"""stratex_backtrader_adapter/analyzers.py

Institutional Quantitative Performance Analyzers inspired by Backtrader:
- SharpeRatio: Annualized risk-adjusted return
- SortinoRatio: Downside deviation risk-adjusted return
- DrawDown: Peak equity, max drawdown (%), drawdown duration
- TradeAnalyzer: Win rate, profit factor, payoff ratio, streaks, avg trade
- SQN: Van Tharp System Quality Number
- CalmarRatio: Annualized return over maximum drawdown
"""

from __future__ import annotations

import math
from typing import Any
from .models import TradeRecord


class BaseAnalyzer:
    """Abstract base analyzer class."""

    def __init__(self, **params) -> None:
        self.params = params

    def notify_bar(self, bar_idx: int, equity: float) -> None:
        pass

    def notify_trade(self, trade: TradeRecord) -> None:
        pass

    def get_analysis(self) -> dict[str, Any]:
        raise NotImplementedError


class SharpeRatio(BaseAnalyzer):
    """Computes annualized Sharpe Ratio from periodic equity curve returns."""

    def __init__(self, riskfree_rate: float = 0.0, timeframe_factor: float = 252.0, **params) -> None:
        super().__init__(**params)
        self.riskfree = float(riskfree_rate)
        self.timeframe_factor = float(timeframe_factor)
        self._equities: list[float] = []

    def notify_bar(self, bar_idx: int, equity: float) -> None:
        self._equities.append(equity)

    def get_analysis(self) -> dict[str, Any]:
        if len(self._equities) < 2:
            return {"sharperatio": 0.0}

        returns = [
            (self._equities[i] - self._equities[i - 1]) / self._equities[i - 1]
            for i in range(1, len(self._equities))
            if self._equities[i - 1] > 0
        ]

        if not returns:
            return {"sharperatio": 0.0}

        mean_ret = sum(returns) / len(returns)
        variance = sum((r - mean_ret) ** 2 for r in returns) / len(returns)
        std_dev = math.sqrt(variance) if variance > 0 else 0.0

        if std_dev == 0:
            sharpe = 0.0
        else:
            rf_per_period = self.riskfree / self.timeframe_factor
            excess_return = mean_ret - rf_per_period
            sharpe = math.sqrt(self.timeframe_factor) * (excess_return / std_dev)

        return {"sharperatio": round(sharpe, 4)}


class SortinoRatio(BaseAnalyzer):
    """Computes Sortino Ratio focusing strictly on downside risk."""

    def __init__(self, riskfree_rate: float = 0.0, timeframe_factor: float = 252.0, **params) -> None:
        super().__init__(**params)
        self.riskfree = float(riskfree_rate)
        self.timeframe_factor = float(timeframe_factor)
        self._equities: list[float] = []

    def notify_bar(self, bar_idx: int, equity: float) -> None:
        self._equities.append(equity)

    def get_analysis(self) -> dict[str, Any]:
        if len(self._equities) < 2:
            return {"sortinoratio": 0.0}

        returns = [
            (self._equities[i] - self._equities[i - 1]) / self._equities[i - 1]
            for i in range(1, len(self._equities))
            if self._equities[i - 1] > 0
        ]
        if not returns:
            return {"sortinoratio": 0.0}

        mean_ret = sum(returns) / len(returns)
        rf_per_period = self.riskfree / self.timeframe_factor
        downside_diffs = [min(0.0, r - rf_per_period) for r in returns]
        downside_variance = sum(d ** 2 for d in downside_diffs) / len(returns)
        downside_dev = math.sqrt(downside_variance) if downside_variance > 0 else 0.0

        if downside_dev == 0:
            sortino = 0.0
        else:
            sortino = math.sqrt(self.timeframe_factor) * ((mean_ret - rf_per_period) / downside_dev)

        return {"sortinoratio": round(sortino, 4)}


class DrawDown(BaseAnalyzer):
    """Computes peak equity, maximum drawdown (%), current drawdown, and duration."""

    def __init__(self, **params) -> None:
        super().__init__(**params)
        self.peak_equity = 0.0
        self.max_drawdown_pct = 0.0
        self.current_drawdown_pct = 0.0
        self.max_duration_bars = 0
        self._current_duration = 0

    def notify_bar(self, bar_idx: int, equity: float) -> None:
        if equity > self.peak_equity:
            self.peak_equity = equity
            self._current_duration = 0
            self.current_drawdown_pct = 0.0
        else:
            self._current_duration += 1
            if self.peak_equity > 0:
                dd_pct = ((self.peak_equity - equity) / self.peak_equity) * 100.0
                self.current_drawdown_pct = dd_pct
                if dd_pct > self.max_drawdown_pct:
                    self.max_drawdown_pct = dd_pct

            if self._current_duration > self.max_duration_bars:
                self.max_duration_bars = self._current_duration

    def get_analysis(self) -> dict[str, Any]:
        return {
            "max": {
                "drawdown": round(self.max_drawdown_pct, 2),
                "duration": self.max_duration_bars,
            },
            "current": {
                "drawdown": round(self.current_drawdown_pct, 2),
                "duration": self._current_duration,
            },
            "peak_equity": round(self.peak_equity, 2),
        }


class TradeAnalyzer(BaseAnalyzer):
    """Analyzes trade metrics: win rate, profit factor, streaks, and payoff ratio."""

    def __init__(self, **params) -> None:
        super().__init__(**params)
        self.trades: list[TradeRecord] = []

    def notify_trade(self, trade: TradeRecord) -> None:
        self.trades.append(trade)

    def get_analysis(self) -> dict[str, Any]:
        total = len(self.trades)
        if total == 0:
            return {
                "total": {"total": 0, "won": 0, "lost": 0},
                "pnl": {"net": {"total": 0.0, "average": 0.0}},
                "win_rate": 0.0,
                "profit_factor": 0.0,
            }

        wins = [t for t in self.trades if t.is_win]
        losses = [t for t in self.trades if t.is_loss]

        gross_profit = sum(t.pnl for t in wins)
        gross_loss = abs(sum(t.pnl for t in losses))
        net_pnl = sum(t.pnl for t in self.trades)

        profit_factor = (gross_profit / gross_loss) if gross_loss > 0 else (999.0 if gross_profit > 0 else 0.0)
        win_rate = (len(wins) / total) * 100.0

        avg_win = (gross_profit / len(wins)) if wins else 0.0
        avg_loss = (gross_loss / len(losses)) if losses else 0.0
        payoff_ratio = (avg_win / avg_loss) if avg_loss > 0 else 0.0

        # Win/loss streaks
        current_streak = 0
        longest_win_streak = 0
        longest_loss_streak = 0

        for t in self.trades:
            if t.is_win:
                current_streak = (current_streak + 1) if current_streak > 0 else 1
                if current_streak > longest_win_streak:
                    longest_win_streak = current_streak
            elif t.is_loss:
                current_streak = (current_streak - 1) if current_streak < 0 else -1
                if abs(current_streak) > longest_loss_streak:
                    longest_loss_streak = abs(current_streak)

        return {
            "total": {
                "total": total,
                "won": len(wins),
                "lost": len(losses),
            },
            "win_rate": round(win_rate, 2),
            "profit_factor": round(profit_factor, 2),
            "payoff_ratio": round(payoff_ratio, 2),
            "pnl": {
                "net": round(net_pnl, 2),
                "average": round(net_pnl / total, 2),
                "gross_profit": round(gross_profit, 2),
                "gross_loss": round(gross_loss, 2),
            },
            "streaks": {
                "won": longest_win_streak,
                "lost": longest_loss_streak,
            },
        }


class SQN(BaseAnalyzer):
    """Van Tharp's System Quality Number: SQN = sqrt(N) * (mean_pnl / std_pnl)."""

    def __init__(self, **params) -> None:
        super().__init__(**params)
        self._pnls: list[float] = []

    def notify_trade(self, trade: TradeRecord) -> None:
        self._pnls.append(trade.pnl)

    def get_analysis(self) -> dict[str, Any]:
        n = len(self._pnls)
        if n < 2:
            return {"sqn": 0.0}

        mean_pnl = sum(self._pnls) / n
        var = sum((p - mean_pnl) ** 2 for p in self._pnls) / n
        std = math.sqrt(var) if var > 0 else 0.0

        if std == 0:
            sqn = 0.0
        else:
            sqn = math.sqrt(n) * (mean_pnl / std)

        return {"sqn": round(sqn, 2)}


class CalmarRatio(BaseAnalyzer):
    """Computes Calmar Ratio = Annualized Return / Max Drawdown."""

    def __init__(self, timeframe_factor: float = 252.0, **params) -> None:
        super().__init__(**params)
        self.timeframe_factor = float(timeframe_factor)
        self._dd_analyzer = DrawDown()
        self._equities: list[float] = []

    def notify_bar(self, bar_idx: int, equity: float) -> None:
        self._equities.append(equity)
        self._dd_analyzer.notify_bar(bar_idx, equity)

    def get_analysis(self) -> dict[str, Any]:
        if len(self._equities) < 2:
            return {"calmar": 0.0}

        start_eq = self._equities[0]
        end_eq = self._equities[-1]
        total_return = (end_eq - start_eq) / start_eq if start_eq > 0 else 0.0

        # Annualize return
        years = len(self._equities) / self.timeframe_factor
        ann_return_pct = (total_return / years * 100.0) if years > 0 else 0.0

        max_dd = self._dd_analyzer.max_drawdown_pct
        calmar = (ann_return_pct / max_dd) if max_dd > 0 else 0.0

        return {"calmar": round(calmar, 2)}
