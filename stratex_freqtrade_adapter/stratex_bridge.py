"""Integration bridge for the existing Stratex backtest engine.

Usage pattern:
    bridge = StratexStrategyBridge(strategy)
    result = bridge.run(df, params)

This bridge deliberately delegates actual simulation, fees, slippage,
risk sizing and long-only behavior to Stratex.
"""

from __future__ import annotations

class StratexStrategyBridge:
    def __init__(self, strategy, backtest_engine_cls):
        self.strategy = strategy
        self.backtest_engine_cls = backtest_engine_cls

    def run(
        self,
        df,
        *,
        strategies=None,
        fee_rate=0.001,
        slippage_rate=0.0005,
        initial_balance=10000.0,
        risk_per_trade=0.005,
        max_open_trades=1,
        symbol="SIM",
        long_only=True,
        intrabar_resolution="conservative",
    ):
        selected = strategies if strategies is not None else [self.strategy]
        engine = self.backtest_engine_cls(
            df,
            selected,
            fee_rate=fee_rate,
            slippage_rate=slippage_rate,
            initial_balance=initial_balance,
            risk_per_trade=risk_per_trade,
            max_open_trades=max_open_trades,
            symbol=symbol,
            long_only=long_only,
            intrabar_resolution=intrabar_resolution,
        )
        trades, equity = engine.run()
        return trades, equity

    def evaluate(
        self,
        df,
        params: dict | None = None,
        *,
        fee_rate: float = 0.001,
        slippage_rate: float = 0.0005,
        initial_balance: float = 10000.0,
        risk_per_trade: float = 0.005,
        max_open_trades: int = 1,
        symbol: str = "SIM",
        long_only: bool = True,
        intrabar_resolution: str = "conservative",
    ) -> dict:
        """Executes simulation on df and returns standardized performance metrics."""
        strat = self.strategy
        if params:
            if hasattr(strat, "set_params"):
                strat.set_params(params)
            elif hasattr(strat, "configure"):
                strat.configure(params)
            elif isinstance(strat, type):
                strat = strat(**params)

        trades, equity = self.run(
            df,
            strategies=[strat],
            fee_rate=fee_rate,
            slippage_rate=slippage_rate,
            initial_balance=initial_balance,
            risk_per_trade=risk_per_trade,
            max_open_trades=max_open_trades,
            symbol=symbol,
            long_only=long_only,
            intrabar_resolution=intrabar_resolution,
        )

        from metrics import calculate_metrics
        m = calculate_metrics(trades, equity, initial_balance=initial_balance)
        
        # Ensure standard keys expected by optimizer
        m["trade_count"] = m.get("total_trades", len(trades))
        raw_mdd = float(m.get("max_dd_pct", 0.0))
        m["max_drawdown_pct"] = raw_mdd / 100.0 if raw_mdd > 1.0 else raw_mdd
        return m

