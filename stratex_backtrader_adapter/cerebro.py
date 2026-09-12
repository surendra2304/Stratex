"""stratex_backtrader_adapter/cerebro.py

Cerebro engine orchestrator inspired by Backtrader:
- Coordinates data feeds, broker, strategy, sizers, and quantitative analyzers
- Drives the sequential bar-by-bar backtest event loop
- Collects and packages complete BacktestResult performance reports
"""

from __future__ import annotations

from typing import Type
import pandas as pd

from .models import BacktestResult, TradeRecord
from .broker import BacktraderBroker, CommissionScheme
from .sizers import BaseSizer, FixedSize
from .analyzers import (
    BaseAnalyzer,
    SharpeRatio,
    SortinoRatio,
    DrawDown,
    TradeAnalyzer,
    SQN,
    CalmarRatio,
)
from .strategy import Strategy


class Cerebro:
    """The central orchestrator for Backtrader simulation in Stratex."""

    def __init__(self) -> None:
        self.datas: dict[str, pd.DataFrame] = {}
        self.strategy_cls: Type[Strategy] | None = None
        self.strategy_params: dict[str, any] = {}
        self.sizer_cls: Type[BaseSizer] = FixedSize
        self.sizer_params: dict[str, any] = {}
        self.analyzers: list[tuple[Type[BaseAnalyzer], str, dict[str, any]]] = []
        self.broker = BacktraderBroker()

        # Add standard default institutional analyzers
        self.add_analyzer(SharpeRatio, "sharpe")
        self.add_analyzer(DrawDown, "drawdown")
        self.add_analyzer(TradeAnalyzer, "trades")
        self.add_analyzer(SQN, "sqn")

    def add_data(self, df: pd.DataFrame, name: str = "DEFAULT") -> None:
        """Attaches a historical OHLCV DataFrame data feed."""
        df_clean = df.copy()
        # Ensure lowercase column naming
        df_clean.columns = [str(c).lower() for c in df_clean.columns]
        if "close" not in df_clean.columns:
            raise ValueError("Data feed must contain a 'close' column.")
        if "open" not in df_clean.columns:
            df_clean["open"] = df_clean["close"]
        if "high" not in df_clean.columns:
            df_clean["high"] = df_clean["close"]
        if "low" not in df_clean.columns:
            df_clean["low"] = df_clean["close"]
        if "volume" not in df_clean.columns:
            df_clean["volume"] = 1.0

        self.datas[name.upper()] = df_clean

    def add_strategy(self, strategy_cls: Type[Strategy], **kwargs) -> None:
        """Sets the strategy to be executed."""
        self.strategy_cls = strategy_cls
        self.strategy_params = kwargs

    def set_sizer(self, sizer_cls: Type[BaseSizer], **kwargs) -> None:
        """Configures position sizing engine."""
        self.sizer_cls = sizer_cls
        self.sizer_params = kwargs

    def add_analyzer(
        self,
        analyzer_cls: Type[BaseAnalyzer],
        name: str | None = None,
        **kwargs,
    ) -> None:
        """Attaches a quantitative performance analyzer."""
        aname = name or analyzer_cls.__name__.lower()
        # Avoid duplicate analyzers with same name
        self.analyzers = [a for a in self.analyzers if a[1] != aname]
        self.analyzers.append((analyzer_cls, aname, kwargs))

    def set_cash(self, cash: float) -> None:
        """Configures starting cash balance."""
        self.broker.initial_cash = float(cash)
        self.broker.cash = float(cash)

    def set_commission(
        self,
        commission_pct: float = 0.0005,
        fixed_fee: float = 0.0,
        slippage_bps: float = 2.0,
    ) -> None:
        """Configures broker fee schedule and execution slippage."""
        self.broker.commission_scheme = CommissionScheme(
            commission_pct=commission_pct,
            fixed_fee=fixed_fee,
            slippage_bps=slippage_bps,
        )

    def run(self) -> BacktestResult:
        """Executes the sequential bar-by-bar backtest simulation."""
        if not self.datas:
            raise ValueError("No data feeds attached to Cerebro. Call add_data() first.")
        if self.strategy_cls is None:
            raise ValueError("No strategy configured in Cerebro. Call add_strategy() first.")

        # 1. Instantiate Sizer and Strategy
        sizer = self.sizer_cls(**self.sizer_params)
        strategy = self.strategy_cls(
            broker=self.broker,
            data_feeds=self.datas,
            sizer=sizer,
            **self.strategy_params,
        )

        # 2. Instantiate Analyzers
        instantiated_analyzers: list[tuple[BaseAnalyzer, str]] = []
        for acls, aname, aparams in self.analyzers:
            instantiated_analyzers.append((acls(**aparams), aname))

        # 3. Strategy Initialization Hooks
        strategy.init()
        strategy.start()

        # 4. Simulation Bar Loop
        total_bars = max(len(df) for df in self.datas.values())
        equity_curve: list[float] = []

        for bar_idx in range(total_bars):
            # Gather current bar market data
            market_data: dict[str, dict[str, float]] = {}
            current_prices: dict[str, float] = {}

            for sym, df in self.datas.items():
                if bar_idx < len(df):
                    row = df.iloc[bar_idx]
                    bar_dict = {
                        "open": float(row["open"]),
                        "high": float(row["high"]),
                        "low": float(row["low"]),
                        "close": float(row["close"]),
                        "volume": float(row.get("volume", 1.0)),
                    }
                    market_data[sym] = bar_dict
                    current_prices[sym] = bar_dict["close"]

            # Broker processes open orders at bar open
            closed_trades = self.broker.process_bar(bar_idx, market_data)
            for trade in closed_trades:
                strategy.notify_trade(trade)
                for analyzer, _ in instantiated_analyzers:
                    analyzer.notify_trade(trade)

            # Advance strategy bar index and trigger strategy next()
            strategy._current_idx = bar_idx
            strategy.next()

            # Record portfolio equity
            curr_equity = self.broker.get_value(current_prices)
            equity_curve.append(curr_equity)

            # Notify analyzers
            for analyzer, _ in instantiated_analyzers:
                analyzer.notify_bar(bar_idx, curr_equity)

        # 5. Simulation Completion Hooks
        strategy.stop()

        # 6. Compile Analyzer Results
        analyzer_results: dict[str, any] = {}
        for analyzer, aname in instantiated_analyzers:
            try:
                analyzer_results[aname] = analyzer.get_analysis()
            except Exception as e:
                analyzer_results[aname] = {"error": str(e)}

        starting_cash = self.broker.initial_cash
        ending_cash = equity_curve[-1] if equity_curve else self.broker.cash
        total_pnl = ending_cash - starting_cash
        total_ret_pct = (total_pnl / starting_cash * 100.0) if starting_cash > 0 else 0.0

        return BacktestResult(
            strategy_name=self.strategy_cls.__name__,
            starting_cash=round(starting_cash, 2),
            ending_cash=round(ending_cash, 2),
            total_pnl=round(total_pnl, 2),
            total_return_pct=round(total_ret_pct, 2),
            trades=list(self.broker._completed_trades),
            analyzers=analyzer_results,
            equity_curve=equity_curve,
        )
