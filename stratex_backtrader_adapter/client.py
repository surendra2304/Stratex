"""stratex_backtrader_adapter/client.py

Unified Facade for Backtrader integration in Stratex:
- Exposes `bt` singleton providing idiomatic Cerebro, Strategy, Sizers, and Analyzers
- Strictly enforces permanent security invariant: LIVE_TRADING_ENABLED = False
"""

from __future__ import annotations

from typing import Type
import pandas as pd

from .models import BacktestResult, BacktestOrder
from .cerebro import Cerebro
from .strategy import Strategy, SMACrossStrategy, RSIStrategy
from .broker import BacktraderBroker, CommissionScheme
from . import sizers
from . import analyzers


class SizersNamespace:
    """Namespace mirroring bt.sizers.*"""
    FixedSize = sizers.FixedSize
    PercentSizer = sizers.PercentSizer
    VolatilitySizer = sizers.VolatilitySizer
    KellySizer = sizers.KellySizer


class AnalyzersNamespace:
    """Namespace mirroring bt.analyzers.*"""
    SharpeRatio = analyzers.SharpeRatio
    SortinoRatio = analyzers.SortinoRatio
    DrawDown = analyzers.DrawDown
    TradeAnalyzer = analyzers.TradeAnalyzer
    SQN = analyzers.SQN
    CalmarRatio = analyzers.CalmarRatio


class BacktraderNativeEngine:
    """Unified engine facade replicating Backtrader's public API."""

    # Permanent Security Invariant
    LIVE_TRADING_ENABLED: bool = False

    def __init__(self) -> None:
        self.Cerebro = Cerebro
        self.Strategy = Strategy
        self.Broker = BacktraderBroker
        self.CommissionScheme = CommissionScheme
        self.sizers = SizersNamespace()
        self.analyzers = AnalyzersNamespace()
        self.strategies = {
            "SMACross": SMACrossStrategy,
            "RSI": RSIStrategy,
        }

    def route_live_order(self, order: BacktestOrder) -> None:
        """Enforces permanent live execution prevention."""
        if not self.LIVE_TRADING_ENABLED:
            raise PermissionError(
                "Live Backtrader order routing is strictly disabled under the Stratex security policy. "
                "LIVE_TRADING_ENABLED = False is permanent."
            )

    def run_backtest(
        self,
        df: pd.DataFrame,
        strategy_cls: Type[Strategy] = SMACrossStrategy,
        sizer_cls: Type[sizers.BaseSizer] = sizers.PercentSizer,
        initial_cash: float = 10000.0,
        commission_pct: float = 0.0005,
        **strategy_params,
    ) -> BacktestResult:
        """Convenience one-line helper to execute a backtest."""
        cerebro = self.Cerebro()
        cerebro.set_cash(initial_cash)
        cerebro.set_commission(commission_pct=commission_pct)
        cerebro.add_data(df, name="MAIN")
        cerebro.add_strategy(strategy_cls, **strategy_params)
        cerebro.set_sizer(sizer_cls)
        return cerebro.run()

    def get_status(self) -> dict[str, any]:
        """Returns overall engine status and catalog of supported modules."""
        return {
            "status": "HEALTHY",
            "live_trading_enabled": self.LIVE_TRADING_ENABLED,
            "sizers": ["FixedSize", "PercentSizer", "VolatilitySizer", "KellySizer"],
            "analyzers": ["SharpeRatio", "SortinoRatio", "DrawDown", "TradeAnalyzer", "SQN", "CalmarRatio"],
            "builtin_strategies": list(self.strategies.keys()),
        }


# Global singleton facade
bt = BacktraderNativeEngine()
