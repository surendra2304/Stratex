"""
analytics/performance_attribution.py — Quantitative Performance Attribution & Factor Decomposition.

Capabilities:
1. Multi-Strategy Contribution: PnL contribution, Win Rate, and Profit Factor per strategy.
2. Asset / Sector Attribution: Decomposition of returns by traded symbol and market sector.
3. Risk-Adjusted Metrics: Strategy-level Sharpe, Sortino, Calmar ratios.
4. Report Generation: Daily and lifetime attribution summaries.
"""

from typing import Any

import pandas as pd

from metrics import calculate_metrics


class PerformanceAttributionEngine:
    """
    Decomposes aggregate trading performance across strategies, symbols, and risk regimes.
    """

    def analyze_strategy_contributions(self, trade_records: list[dict[str, Any]]) -> dict[str, Any]:
        """
        Groups closed trades by strategy and calculates isolated performance metrics.
        """
        if not trade_records:
            return {}

        df = pd.DataFrame(trade_records)
        if "strategy" not in df.columns:
            df["strategy"] = "default_strategy"

        strategies = df["strategy"].unique()
        total_pnl = float(df["net_pnl"].sum()) if "net_pnl" in df.columns else 0.0

        strategy_results = {}
        for strat in strategies:
            strat_trades = df[df["strategy"] == strat].to_dict(orient="records")
            metrics = calculate_metrics(strat_trades, None, initial_balance=10000.0)
            strat_pnl = float(metrics.get("net_pnl", 0.0))
            pnl_contrib_pct = (strat_pnl / abs(total_pnl)) * 100.0 if abs(total_pnl) > 0 else 0.0

            strategy_results[strat] = {
                "total_trades": metrics.get("total_trades", 0),
                "win_rate_pct": round(metrics.get("win_rate", 0.0), 2),
                "profit_factor": round(float(metrics.get("profit_factor", 0.0)), 2) if metrics.get("profit_factor") else 0.0,
                "net_pnl": round(strat_pnl, 2),
                "pnl_contribution_pct": round(pnl_contrib_pct, 2),
                "sharpe_ratio": round(metrics.get("sharpe", 0.0), 2)
            }

        return {
            "total_portfolio_pnl": round(total_pnl, 2),
            "strategy_breakdown": strategy_results
        }

    def analyze_symbol_contributions(self, trade_records: list[dict[str, Any]]) -> dict[str, Any]:
        """
        Decomposes performance by coin symbol.
        """
        if not trade_records:
            return {}

        df = pd.DataFrame(trade_records)
        if "symbol" not in df.columns:
            return {}

        symbol_results = {}
        for sym, group in df.groupby("symbol"):
            sym_pnl = float(group["net_pnl"].sum()) if "net_pnl" in group.columns else 0.0
            wins = len(group[group["net_pnl"] > 0]) if "net_pnl" in group.columns else 0
            total = len(group)
            symbol_results[sym] = {
                "trades": total,
                "net_pnl": round(sym_pnl, 2),
                "win_rate_pct": round((wins / total) * 100.0, 1) if total > 0 else 0.0
            }

        return symbol_results
