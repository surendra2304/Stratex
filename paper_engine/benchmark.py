"""
paper_engine/benchmark.py

Scientifically rigorous benchmarking for forward validation.

Key requirements:
- Monte Carlo must use the SAME CostEngine as the actual strategy.
- Random trades must have the same structural constraints (hold periods, sizing, leverage).
- Two-leg pairs and funding strategies must model BOTH legs.
- Must be reproducible via explicit random seed.
- Reports: median, p5, p95, fraction beating strategy.
"""

import numpy as np
import pandas as pd

from research_phase9.cost_engine import CostEngine


class BenchmarkComparators:
    """
    Computes rigorous benchmark metrics for forward validation comparison.

    IMPORTANT: Passing these benchmarks proves NOTHING about profitability.
    It only establishes whether the strategy outperforms a null hypothesis.
    Statistical significance requires sufficient sample size — see ForwardValidationReport.
    """

    @staticmethod
    def buy_and_hold(
        df: pd.DataFrame,
        starting_capital: float,
        cost_engine: CostEngine | None = None,
    ) -> dict[str, float]:
        """
        Buy-and-Hold benchmark: purchase at first available close, sell at last.
        Applies realistic entry/exit costs using the provided CostEngine.
        """
        if df.empty:
            return {"net_pnl": 0.0, "return_pct": 0.0, "gross_pnl": 0.0, "total_cost": 0.0}

        cost = cost_engine or CostEngine.get_binance_taker_config()
        start_price = df["close"].iloc[0]
        end_price = df["close"].iloc[-1]
        qty = starting_capital / start_price

        gross_pnl = (end_price - start_price) * qty
        # One round-trip cost on total notional
        notional = starting_capital
        total_cost = notional * cost.get_total_friction()
        net_pnl = gross_pnl - total_cost

        return {
            "gross_pnl": round(gross_pnl, 6),
            "total_cost": round(total_cost, 6),
            "net_pnl": round(net_pnl, 6),
            "return_pct": round(net_pnl / starting_capital * 100, 4),
        }

    @staticmethod
    def zero_trade_benchmark(starting_capital: float) -> dict[str, float]:
        """Trivial baseline: doing nothing. Net PnL = 0."""
        return {"net_pnl": 0.0, "return_pct": 0.0}

    @staticmethod
    def random_entry_monte_carlo(
        df: pd.DataFrame,
        starting_capital: float,
        cost_engine: CostEngine | None = None,
        n_trades: int = 10,
        hold_bars: int = 5,
        leverage: float = 1.0,
        iterations: int = 1000,
        random_seed: int = 42,
        strategy_net_pnl: float | None = None,
    ) -> dict[str, float]:
        """
        Simulated random-entry Monte Carlo benchmark.

        Uses the SAME CostEngine as the actual strategy. Each simulated trade:
          - Picks a random entry bar from df.
          - Enters LONG or SHORT with equal probability.
          - Exits after exactly hold_bars bars (fixed hold period).
          - Applies entry_fee, exit_fee, entry_slip, exit_slip, spread on actual notional.
          - Uses the specified leverage for position sizing.

        Parameters
        ----------
        df : OHLCV DataFrame (chronologically ordered, no shuffle)
        starting_capital : Capital allocated to this strategy slice.
        cost_engine : Must be the SAME CostEngine used by the actual strategy.
        n_trades : Number of trades per simulation iteration.
        hold_bars : Fixed hold period in bars (must match strategy's typical hold).
        leverage : Position leverage (default 1x).
        iterations : Number of Monte Carlo iterations.
        random_seed : Fixed seed for reproducibility.
        strategy_net_pnl : If provided, also computes fraction of sims beating the strategy.

        Returns
        -------
        dict with: median_pnl, p05_pnl, p95_pnl, fraction_beating_strategy,
                   mean_pnl, std_pnl, total_cost_median, random_seed
        """
        if df.empty or len(df) < hold_bars + 1:
            return {
                "median_pnl": 0.0, "p05_pnl": 0.0, "p95_pnl": 0.0,
                "fraction_beating_strategy": None, "mean_pnl": 0.0,
                "std_pnl": 0.0, "total_cost_median": 0.0,
                "random_seed": random_seed, "error": "INSUFFICIENT_DATA",
            }

        cost = cost_engine or CostEngine.get_binance_taker_config()
        rng = np.random.default_rng(random_seed)
        closes = df["close"].values
        n = len(closes)
        max_entry = n - hold_bars - 1  # must have room for hold + exit

        if max_entry <= 0:
            return {
                "median_pnl": 0.0, "p05_pnl": 0.0, "p95_pnl": 0.0,
                "fraction_beating_strategy": None, "mean_pnl": 0.0,
                "std_pnl": 0.0, "total_cost_median": 0.0,
                "random_seed": random_seed, "error": "INSUFFICIENT_BARS",
            }

        sim_pnls = []
        sim_costs = []

        for _ in range(iterations):
            iter_pnl = 0.0
            iter_cost = 0.0

            entry_indices = rng.integers(0, max_entry, size=n_trades)
            directions = rng.choice([1.0, -1.0], size=n_trades)

            for entry_idx, direction in zip(entry_indices, directions):
                entry_price = closes[entry_idx]
                exit_idx = int(entry_idx) + hold_bars
                exit_price = closes[exit_idx]

                # Position sizing: allocate equal capital per trade
                capital_per_trade = starting_capital / n_trades
                notional = capital_per_trade * leverage
                qty = notional / entry_price

                # Apply slippage to execution prices
                eff_entry = entry_price * (1 + cost.entry_slip * direction)
                eff_exit = exit_price * (1 - cost.exit_slip * direction)

                # Gross PnL
                gross_pnl = direction * (eff_exit - eff_entry) * qty

                # Costs on notional
                entry_fee_cost = notional * cost.entry_fee
                exit_fee_cost = notional * cost.exit_fee
                spread_cost = notional * cost.spread

                total_cost = entry_fee_cost + exit_fee_cost + spread_cost
                net_pnl = gross_pnl - total_cost

                iter_pnl += net_pnl
                iter_cost += total_cost

            sim_pnls.append(iter_pnl)
            sim_costs.append(iter_cost)

        sim_pnls = np.array(sim_pnls)
        sim_costs = np.array(sim_costs)

        fraction_beating = None
        if strategy_net_pnl is not None:
            fraction_beating = float(np.mean(sim_pnls > strategy_net_pnl))

        return {
            "median_pnl": float(np.median(sim_pnls)),
            "mean_pnl": float(np.mean(sim_pnls)),
            "std_pnl": float(np.std(sim_pnls)),
            "p05_pnl": float(np.percentile(sim_pnls, 5)),
            "p95_pnl": float(np.percentile(sim_pnls, 95)),
            "total_cost_median": float(np.median(sim_costs)),
            "fraction_beating_strategy": fraction_beating,
            "random_seed": random_seed,
            "iterations": iterations,
            "n_trades": n_trades,
            "hold_bars": hold_bars,
            "leverage": leverage,
            "cost_engine": cost.get_report_dict(),
        }

    @staticmethod
    def pairs_random_entry_monte_carlo(
        df_a: pd.DataFrame,
        df_b: pd.DataFrame,
        starting_capital: float,
        cost_engine: CostEngine | None = None,
        n_pairs: int = 10,
        hold_bars: int = 5,
        leverage: float = 1.0,
        iterations: int = 1000,
        random_seed: int = 42,
        strategy_net_pnl: float | None = None,
    ) -> dict[str, float]:
        """
        Monte Carlo for two-leg pairs strategies.
        Models entry/exit costs for BOTH Leg A and Leg B independently.
        """
        if df_a.empty or df_b.empty:
            return {"median_pnl": 0.0, "error": "EMPTY_DATA"}

        cost = cost_engine or CostEngine.get_binance_taker_config()
        rng = np.random.default_rng(random_seed)
        closes_a = df_a["close"].values
        closes_b = df_b["close"].values
        n = min(len(closes_a), len(closes_b))
        max_entry = n - hold_bars - 1

        if max_entry <= 0:
            return {"median_pnl": 0.0, "error": "INSUFFICIENT_BARS"}

        sim_pnls = []
        for _ in range(iterations):
            iter_pnl = 0.0
            entry_indices = rng.integers(0, max_entry, size=n_pairs)
            # Pairs: always Long A / Short B or Short A / Long B
            directions = rng.choice([1.0, -1.0], size=n_pairs)

            for entry_idx, dir_a in zip(entry_indices, directions):
                dir_b = -dir_a
                capital_per_pair = starting_capital / n_pairs

                # Leg A
                ea = closes_a[entry_idx]
                xa = closes_a[int(entry_idx) + hold_bars]
                notional_a = capital_per_pair * leverage / 2
                qty_a = notional_a / ea
                eff_ea = ea * (1 + cost.entry_slip * dir_a)
                eff_xa = xa * (1 - cost.exit_slip * dir_a)
                pnl_a = dir_a * (eff_xa - eff_ea) * qty_a
                cost_a = notional_a * (cost.entry_fee + cost.exit_fee + cost.spread)

                # Leg B
                eb = closes_b[entry_idx]
                xb = closes_b[int(entry_idx) + hold_bars]
                notional_b = capital_per_pair * leverage / 2
                qty_b = notional_b / eb
                eff_eb = eb * (1 + cost.entry_slip * dir_b)
                eff_xb = xb * (1 - cost.exit_slip * dir_b)
                pnl_b = dir_b * (eff_xb - eff_eb) * qty_b
                cost_b = notional_b * (cost.entry_fee + cost.exit_fee + cost.spread)

                iter_pnl += (pnl_a - cost_a) + (pnl_b - cost_b)

            sim_pnls.append(iter_pnl)

        sim_pnls = np.array(sim_pnls)
        fraction_beating = None
        if strategy_net_pnl is not None:
            fraction_beating = float(np.mean(sim_pnls > strategy_net_pnl))

        return {
            "median_pnl": float(np.median(sim_pnls)),
            "mean_pnl": float(np.mean(sim_pnls)),
            "p05_pnl": float(np.percentile(sim_pnls, 5)),
            "p95_pnl": float(np.percentile(sim_pnls, 95)),
            "fraction_beating_strategy": fraction_beating,
            "random_seed": random_seed,
            "cost_engine": cost.get_report_dict(),
        }

    @staticmethod
    def funding_arb_random_monte_carlo(
        spot_df: pd.DataFrame,
        perp_df: pd.DataFrame,
        funding_rates: np.ndarray,
        starting_capital: float,
        cost_engine: CostEngine | None = None,
        n_entries: int = 10,
        hold_funding_periods: int = 3,
        funding_interval_bars: int = 480,
        iterations: int = 1000,
        random_seed: int = 42,
        strategy_net_pnl: float | None = None,
    ) -> dict[str, float]:
        """
        Monte Carlo for funding arbitrage.
        Models BOTH spot and perpetual legs plus funding payments.
        Funding arbitrage cost structure:
          - Spot: entry_fee + exit_fee + slippage (no funding payment)
          - Perp: entry_fee + exit_fee + slippage + funding payment per period
        """
        cost = cost_engine or CostEngine.get_binance_taker_config()
        rng = np.random.default_rng(random_seed)

        if spot_df.empty or perp_df.empty or len(funding_rates) == 0:
            return {"median_pnl": 0.0, "error": "INSUFFICIENT_DATA"}

        spot_closes = spot_df["close"].values
        perp_closes = perp_df["close"].values
        n = min(len(spot_closes), len(perp_closes))
        hold_bars = hold_funding_periods * funding_interval_bars
        max_entry = n - hold_bars - 1

        if max_entry <= 0:
            return {"median_pnl": 0.0, "error": "INSUFFICIENT_BARS"}

        sim_pnls = []
        for _ in range(iterations):
            iter_pnl = 0.0
            entry_indices = rng.integers(0, max_entry, size=n_entries)

            for entry_idx in entry_indices:
                capital_per_arb = starting_capital / n_entries
                notional = capital_per_arb / 2  # split equally between spot and perp

                # Spot: LONG
                sp_entry = spot_closes[entry_idx]
                sp_exit = spot_closes[int(entry_idx) + hold_bars]
                qty_spot = notional / sp_entry
                eff_sp_entry = sp_entry * (1 + cost.entry_slip)
                eff_sp_exit = sp_exit * (1 - cost.exit_slip)
                pnl_spot = (eff_sp_exit - eff_sp_entry) * qty_spot
                cost_spot = notional * (cost.entry_fee + cost.exit_fee + cost.spread)

                # Perp: SHORT
                pp_entry = perp_closes[entry_idx]
                pp_exit = perp_closes[int(entry_idx) + hold_bars]
                qty_perp = notional / pp_entry
                eff_pp_entry = pp_entry * (1 - cost.entry_slip)
                eff_pp_exit = pp_exit * (1 + cost.exit_slip)
                pnl_perp = (eff_pp_entry - eff_pp_exit) * qty_perp
                cost_perp = notional * (cost.entry_fee + cost.exit_fee + cost.spread)

                # Funding: received by SHORT position when rate > 0
                # Pick random funding rates for the hold period
                fund_idx = rng.integers(0, len(funding_rates), size=hold_funding_periods)
                funding_pnl = float(np.sum(funding_rates[fund_idx])) * notional

                iter_pnl += (pnl_spot - cost_spot) + (pnl_perp - cost_perp) + funding_pnl

            sim_pnls.append(iter_pnl)

        sim_pnls = np.array(sim_pnls)
        fraction_beating = None
        if strategy_net_pnl is not None:
            fraction_beating = float(np.mean(sim_pnls > strategy_net_pnl))

        return {
            "median_pnl": float(np.median(sim_pnls)),
            "mean_pnl": float(np.mean(sim_pnls)),
            "p05_pnl": float(np.percentile(sim_pnls, 5)),
            "p95_pnl": float(np.percentile(sim_pnls, 95)),
            "fraction_beating_strategy": fraction_beating,
            "random_seed": random_seed,
            "cost_engine": cost.get_report_dict(),
        }
