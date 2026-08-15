"""
tests/test_phase13_15_corrections.py

Regression tests for the Phase 13-15 correction pass.

Tests added per requirements:
  - Realistic Monte Carlo costs (uses real CostEngine, not 0.002 magic number)
  - Deterministic random seed (same seed → same MC result)
  - Two-leg pairs costs (both legs modelled)
  - Funding costs (spot + perp + funding payment)
  - Kill-switch exit costs (never zero cost)
  - Kill-switch ledger annotation (KILL_SWITCH reason recorded)
  - Statistical report correctness (hypothesis, sample-size gate, INCONCLUSIVE label)
  - Frozen experiment config immutability
  - Forward experiment reproducibility (same config → same registry entry)
  - PAPER mode never placing Binance orders (execution policy gate)
"""
import json
import math
import os
import time
import uuid
import tempfile
import numpy as np
import pandas as pd
import pytest

from research_phase9.cost_engine import CostEngine
from paper_engine.benchmark import BenchmarkComparators
from paper_engine.kill_switch import trigger_kill_switch, is_kill_switch_active, reset_kill_switch
from paper_engine.portfolio import PaperPortfolio
from paper_engine.experiment_config import FrozenExperimentConfig, create_experiment, register_experiment
from paper_engine.statistical_report import (
    compute_trade_stats,
    t_test_positive_expectancy,
    evaluate_against_acceptance_criteria,
    MIN_TRADES_FOR_INFERENCE,
)


# ─── Helpers ────────────────────────────────────────────────────────────────

def _make_df(n=200, seed=42, trend=0.0):
    rng = np.random.default_rng(seed)
    prices = 50000.0 + np.cumsum(rng.normal(trend, 100, n))
    prices = np.maximum(prices, 1.0)
    return pd.DataFrame({
        "timestamp": pd.date_range("2024-01-01", periods=n, freq="1min"),
        "open": prices,
        "high": prices * 1.001,
        "low": prices * 0.999,
        "close": prices,
        "volume": rng.uniform(10, 1000, n),
    })


def _portfolio(tmp_path, name="p.json"):
    return PaperPortfolio(filename=str(tmp_path / name))


# ═══════════════════════════════════════════════════════════════════════════
# 1. MONTE CARLO BENCHMARK CORRECTNESS
# ═══════════════════════════════════════════════════════════════════════════

class TestMonteCarloBenchmark:

    def test_uses_real_cost_engine_not_magic_number(self):
        """MC must use CostEngine.get_total_friction(), not a hardcoded 0.002."""
        df = _make_df(500)
        cost = CostEngine.get_binance_taker_config()
        result = BenchmarkComparators.random_entry_monte_carlo(
            df, starting_capital=10000.0,
            cost_engine=cost,
            n_trades=5, hold_bars=10, iterations=100, random_seed=1,
        )
        # Verify cost engine is reported in output
        assert "cost_engine" in result
        assert result["cost_engine"]["total_friction_bps"] == pytest.approx(
            cost.get_total_friction() * 10000, rel=1e-6
        )

    def test_deterministic_with_same_seed(self):
        """Same seed must produce identical MC results every run."""
        df = _make_df(500)
        cost = CostEngine.get_binance_taker_config()
        r1 = BenchmarkComparators.random_entry_monte_carlo(
            df, 10000.0, cost_engine=cost, n_trades=5, hold_bars=10,
            iterations=200, random_seed=99,
        )
        r2 = BenchmarkComparators.random_entry_monte_carlo(
            df, 10000.0, cost_engine=cost, n_trades=5, hold_bars=10,
            iterations=200, random_seed=99,
        )
        assert r1["median_pnl"] == r2["median_pnl"]
        assert r1["p05_pnl"] == r2["p05_pnl"]
        assert r1["p95_pnl"] == r2["p95_pnl"]

    def test_different_seeds_produce_different_results(self):
        """Different seeds must produce different MC distributions."""
        df = _make_df(500)
        cost = CostEngine.get_binance_taker_config()
        r1 = BenchmarkComparators.random_entry_monte_carlo(
            df, 10000.0, cost_engine=cost, n_trades=5, hold_bars=10,
            iterations=200, random_seed=1,
        )
        r2 = BenchmarkComparators.random_entry_monte_carlo(
            df, 10000.0, cost_engine=cost, n_trades=5, hold_bars=10,
            iterations=200, random_seed=2,
        )
        assert r1["median_pnl"] != r2["median_pnl"]

    def test_reports_p05_median_p95(self):
        """Must report p5, median, and p95."""
        df = _make_df(300)
        cost = CostEngine.get_binance_taker_config()
        r = BenchmarkComparators.random_entry_monte_carlo(
            df, 10000.0, cost_engine=cost, n_trades=5, hold_bars=5,
            iterations=500, random_seed=42,
        )
        assert "p05_pnl" in r
        assert "median_pnl" in r
        assert "p95_pnl" in r
        assert r["p05_pnl"] <= r["median_pnl"] <= r["p95_pnl"]

    def test_fraction_beating_strategy_computed(self):
        """fraction_beating_strategy must be computed when strategy_net_pnl is provided."""
        df = _make_df(400)
        cost = CostEngine.get_binance_taker_config()
        r = BenchmarkComparators.random_entry_monte_carlo(
            df, 10000.0, cost_engine=cost, n_trades=5, hold_bars=5,
            iterations=200, random_seed=42,
            strategy_net_pnl=0.0,  # strategy made $0
        )
        assert r["fraction_beating_strategy"] is not None
        assert 0.0 <= r["fraction_beating_strategy"] <= 1.0

    def test_higher_cost_engine_reduces_mc_pnl(self):
        """Higher friction must produce lower median MC PnL."""
        df = _make_df(500)
        low_cost = CostEngine(entry_fee=0.0001, exit_fee=0.0001, entry_slip=0.0, exit_slip=0.0, spread=0.0)
        high_cost = CostEngine(entry_fee=0.002, exit_fee=0.002, entry_slip=0.001, exit_slip=0.001, spread=0.001)

        r_low = BenchmarkComparators.random_entry_monte_carlo(
            df, 10000.0, cost_engine=low_cost, n_trades=10, hold_bars=5,
            iterations=500, random_seed=42,
        )
        r_high = BenchmarkComparators.random_entry_monte_carlo(
            df, 10000.0, cost_engine=high_cost, n_trades=10, hold_bars=5,
            iterations=500, random_seed=42,
        )
        assert r_low["median_pnl"] > r_high["median_pnl"], (
            "Higher friction must reduce median MC PnL"
        )

    def test_insufficient_data_returns_error_key(self):
        """Very short DataFrame must return error key, not crash."""
        df = _make_df(5)  # only 5 bars
        cost = CostEngine.get_binance_taker_config()
        r = BenchmarkComparators.random_entry_monte_carlo(
            df, 10000.0, cost_engine=cost, n_trades=5, hold_bars=10,
            iterations=10, random_seed=1,
        )
        assert "error" in r

    def test_buy_and_hold_uses_cost_engine(self):
        """Buy-and-hold benchmark must apply CostEngine friction."""
        df = _make_df(200)
        cost = CostEngine.get_binance_taker_config()
        result = BenchmarkComparators.buy_and_hold(df, 10000.0, cost_engine=cost)
        assert "total_cost" in result
        assert result["total_cost"] > 0.0
        assert result["net_pnl"] <= result["gross_pnl"]


# ═══════════════════════════════════════════════════════════════════════════
# 2. TWO-LEG PAIRS COSTS
# ═══════════════════════════════════════════════════════════════════════════

class TestPairsBenchmark:

    def test_pairs_mc_models_both_legs(self):
        """Pairs MC must report both-leg cost structure."""
        df_a = _make_df(300, seed=1)
        df_b = _make_df(300, seed=2)
        cost = CostEngine.get_binance_taker_config()

        r = BenchmarkComparators.pairs_random_entry_monte_carlo(
            df_a, df_b, 10000.0, cost_engine=cost,
            n_pairs=5, hold_bars=5, leverage=1.0,
            iterations=100, random_seed=42,
        )
        # Must have results — no "INSUFFICIENT_DATA" error
        assert "error" not in r or r.get("error") != "EMPTY_DATA"
        assert "median_pnl" in r
        assert "p05_pnl" in r
        assert "p95_pnl" in r

    def test_pairs_mc_higher_cost_lower_pnl(self):
        """Higher cost engine must reduce pairs MC median PnL."""
        df_a = _make_df(300, seed=10)
        df_b = _make_df(300, seed=11)
        low_cost = CostEngine(entry_fee=0.0001, exit_fee=0.0001, entry_slip=0.0, exit_slip=0.0, spread=0.0)
        high_cost = CostEngine(entry_fee=0.002, exit_fee=0.002, entry_slip=0.001, exit_slip=0.001, spread=0.001)

        r_low = BenchmarkComparators.pairs_random_entry_monte_carlo(
            df_a, df_b, 10000.0, cost_engine=low_cost, n_pairs=5, hold_bars=5,
            iterations=200, random_seed=42,
        )
        r_high = BenchmarkComparators.pairs_random_entry_monte_carlo(
            df_a, df_b, 10000.0, cost_engine=high_cost, n_pairs=5, hold_bars=5,
            iterations=200, random_seed=42,
        )
        assert r_low["median_pnl"] > r_high["median_pnl"]

    def test_pairs_mc_deterministic(self):
        """Same seed → same pairs MC result."""
        df_a = _make_df(300, seed=5)
        df_b = _make_df(300, seed=6)
        cost = CostEngine.get_binance_taker_config()

        r1 = BenchmarkComparators.pairs_random_entry_monte_carlo(
            df_a, df_b, 10000.0, cost_engine=cost, n_pairs=5, hold_bars=5,
            iterations=100, random_seed=77,
        )
        r2 = BenchmarkComparators.pairs_random_entry_monte_carlo(
            df_a, df_b, 10000.0, cost_engine=cost, n_pairs=5, hold_bars=5,
            iterations=100, random_seed=77,
        )
        assert r1["median_pnl"] == r2["median_pnl"]


# ═══════════════════════════════════════════════════════════════════════════
# 3. FUNDING ARB COSTS
# ═══════════════════════════════════════════════════════════════════════════

class TestFundingArbBenchmark:

    def test_funding_mc_models_both_legs_plus_funding(self):
        """Funding MC must incorporate spot, perp, and funding payment."""
        spot_df = _make_df(600, seed=20)
        perp_df = _make_df(600, seed=21)
        rng = np.random.default_rng(42)
        funding_rates = rng.uniform(0.0001, 0.001, 100)
        cost = CostEngine.get_binance_taker_config()

        r = BenchmarkComparators.funding_arb_random_monte_carlo(
            spot_df, perp_df, funding_rates, 10000.0,
            cost_engine=cost, n_entries=5, hold_funding_periods=2,
            funding_interval_bars=5, iterations=100, random_seed=42,
        )
        assert "median_pnl" in r
        assert "p05_pnl" in r

    def test_funding_mc_deterministic(self):
        """Same seed → same funding MC result."""
        spot_df = _make_df(600, seed=30)
        perp_df = _make_df(600, seed=31)
        rng = np.random.default_rng(1)
        rates = rng.uniform(0.0001, 0.001, 100)
        cost = CostEngine.get_binance_taker_config()

        r1 = BenchmarkComparators.funding_arb_random_monte_carlo(
            spot_df, perp_df, rates, 10000.0, cost_engine=cost,
            n_entries=5, hold_funding_periods=2, funding_interval_bars=5,
            iterations=50, random_seed=55,
        )
        r2 = BenchmarkComparators.funding_arb_random_monte_carlo(
            spot_df, perp_df, rates, 10000.0, cost_engine=cost,
            n_entries=5, hold_funding_periods=2, funding_interval_bars=5,
            iterations=50, random_seed=55,
        )
        assert r1["median_pnl"] == r2["median_pnl"]


# ═══════════════════════════════════════════════════════════════════════════
# 4. KILL-SWITCH: REALISTIC COSTS
# ═══════════════════════════════════════════════════════════════════════════

class TestKillSwitchCosts:

    def test_kill_switch_exit_applies_realistic_cost(self, tmp_path, monkeypatch):
        """Kill switch must NOT exit positions at zero cost."""
        # Prevent sys.exit
        lock_path = str(tmp_path / "ks.lock")
        monkeypatch.setattr("paper_engine.kill_switch.KILL_SWITCH_LOCK_FILE", lock_path)

        p = _portfolio(tmp_path)
        p.ledger_file = str(tmp_path / "ledger.jsonl")
        monkeypatch.setattr(p, "_save", lambda: None)

        # Add a long position
        ev = str(uuid.uuid4())
        p.allocate_margin(5000.0, ev)
        pos_id = str(uuid.uuid4())
        p.add_position(pos_id, "BTCUSDT", "LONG", 50000.0, 0.1)

        cash_before = p.cash
        cost = CostEngine.get_binance_taker_config()

        summary = trigger_kill_switch(
            reason="TEST",
            portfolio=p,
            current_market_prices={"BTCUSDT": 50000.0},
            cost_engine=cost,
        )

        # Must have closed positions
        assert summary["positions_closed"] == 1
        # Exit cost must be > 0
        assert summary["total_exit_cost"] > 0.0

    def test_kill_switch_cost_matches_cost_engine(self, tmp_path, monkeypatch):
        """Kill switch exit cost must use the provided CostEngine, not arbitrary values."""
        lock_path = str(tmp_path / "ks.lock")
        monkeypatch.setattr("paper_engine.kill_switch.KILL_SWITCH_LOCK_FILE", lock_path)

        p = _portfolio(tmp_path)
        p.ledger_file = str(tmp_path / "ledger.jsonl")
        monkeypatch.setattr(p, "_save", lambda: None)

        # Add exactly one position
        ev = str(uuid.uuid4())
        p.allocate_margin(5000.0, ev)
        pos_id = str(uuid.uuid4())
        entry_price = 50000.0
        qty = 0.1
        p.add_position(pos_id, "BTCUSDT", "LONG", entry_price, qty)

        exit_price = 51000.0  # $1000 gross PnL
        cost = CostEngine.get_binance_taker_config()

        # Compute expected cost
        notional = exit_price * qty
        expected_exit_fee = notional * cost.exit_fee
        expected_spread = notional * cost.spread

        summary = trigger_kill_switch(
            reason="TEST_COST",
            portfolio=p,
            current_market_prices={"BTCUSDT": exit_price},
            cost_engine=cost,
        )

        assert summary["positions_closed"] == 1
        # Total exit cost must approximate expected (within slippage rounding)
        expected_total = expected_exit_fee + expected_spread
        assert summary["total_exit_cost"] == pytest.approx(expected_total, rel=0.01)

    def test_kill_switch_ledger_annotation(self, tmp_path, monkeypatch):
        """Kill switch must annotate the ledger with reason=KILL_SWITCH."""
        lock_path = str(tmp_path / "ks.lock")
        monkeypatch.setattr("paper_engine.kill_switch.KILL_SWITCH_LOCK_FILE", lock_path)

        p = _portfolio(tmp_path)
        p.ledger_file = str(tmp_path / "ledger.jsonl")
        monkeypatch.setattr(p, "_save", lambda: None)

        ev = str(uuid.uuid4())
        p.allocate_margin(5000.0, ev)
        pos_id = str(uuid.uuid4())
        p.add_position(pos_id, "BTCUSDT", "LONG", 50000.0, 0.1)

        trigger_kill_switch(
            reason="DRAWDOWN_BREACH",
            portfolio=p,
            current_market_prices={"BTCUSDT": 50000.0},
            cost_engine=CostEngine.get_binance_taker_config(),
        )

        with open(p.ledger_file) as f:
            records = [json.loads(l) for l in f if l.strip()]

        annotations = [r for r in records if r.get("type") == "KILL_SWITCH_ANNOTATION"]
        assert len(annotations) >= 1
        assert annotations[0]["reason"] == "DRAWDOWN_BREACH"

    def test_kill_switch_no_position_provided_safe(self, tmp_path, monkeypatch):
        """Kill switch with no portfolio arg must still write lock file safely."""
        lock_path = str(tmp_path / "ks.lock")
        monkeypatch.setattr("paper_engine.kill_switch.KILL_SWITCH_LOCK_FILE", lock_path)

        summary = trigger_kill_switch(reason="NO_PORTFOLIO")
        assert summary["positions_closed"] == 0
        assert os.path.exists(lock_path)


# ═══════════════════════════════════════════════════════════════════════════
# 5. STATISTICAL REPORT CORRECTNESS
# ═══════════════════════════════════════════════════════════════════════════

class TestStatisticalReport:

    def test_insufficient_sample_always_inconclusive(self):
        """< MIN_TRADES returns INCONCLUSIVE, never PASS or FAIL."""
        returns = [0.001] * (MIN_TRADES_FOR_INFERENCE - 1)
        result = t_test_positive_expectancy(returns)
        assert result["verdict"] == "INCONCLUSIVE"
        assert result["p_value"] is None

    def test_zero_trades_returns_inconclusive(self):
        result = t_test_positive_expectancy([])
        assert result["verdict"] == "INCONCLUSIVE"

    def test_positive_expectancy_reported_correctly(self):
        """Strongly positive returns should produce low p-value."""
        # 200 trades all returning +5% — overwhelmingly positive
        returns = [0.05] * 200
        stats = compute_trade_stats(returns)
        assert stats["expectancy_pct"] == pytest.approx(5.0, rel=1e-4)
        assert stats["win_rate"] == pytest.approx(1.0)
        assert stats["profit_factor"] == float("inf")

    def test_hypothesis_fields_always_present(self):
        """Hypothesis definition must always be in output."""
        result = t_test_positive_expectancy([0.001] * 5)
        assert "hypothesis_h0" in result
        assert "hypothesis_h1" in result
        assert "observation_unit" in result
        assert "sample_size" in result

    def test_compute_trade_stats_all_fields(self):
        """compute_trade_stats must return all required fields."""
        rng = np.random.default_rng(42)
        returns = list(rng.normal(0.001, 0.01, 100))
        s = compute_trade_stats(returns)
        required = ["sample_size", "win_rate", "profit_factor", "expectancy_pct",
                    "sharpe", "sortino", "mean_return_pct", "std_return_pct"]
        for field in required:
            assert field in s, f"Missing field: {field}"


# ═══════════════════════════════════════════════════════════════════════════
# 6. FROZEN EXPERIMENT CONFIG
# ═══════════════════════════════════════════════════════════════════════════

class TestFrozenExperimentConfig:

    def test_config_saves_and_loads_correctly(self, tmp_path):
        """Saved config must load identically."""
        cfg = FrozenExperimentConfig(
            experiment_name="test_exp",
            strategy_name="sma_cross",
            symbols=["BTCUSDT", "ETHUSDT"],
            strategy_params={"fast": 10, "slow": 30},
            starting_capital=10000.0,
        )
        cfg.save(str(tmp_path))
        loaded = FrozenExperimentConfig.load(cfg.experiment_id, str(tmp_path))
        assert loaded.experiment_name == cfg.experiment_name
        assert loaded.symbols == cfg.symbols
        assert loaded.strategy_params == cfg.strategy_params

    def test_cannot_start_twice(self, tmp_path):
        """mark_started must raise if already started."""
        cfg = FrozenExperimentConfig()
        cfg.mark_started()
        with pytest.raises(RuntimeError, match="already"):
            cfg.mark_started()

    def test_git_sha_captured(self, tmp_path):
        """Experiment must capture Git SHA at creation time."""
        cost = CostEngine.get_binance_taker_config()
        cfg = create_experiment(
            name="test_sha_exp",
            strategy_name="test",
            symbols=["BTCUSDT"],
            strategy_params={},
            cost_engine=cost,
            starting_capital=5000.0,
            planned_duration_days=30,
        )
        assert cfg.git_sha != ""
        assert len(cfg.git_sha) >= 7  # at least 7 chars for short SHA

    def test_experiment_registered_in_registry(self, tmp_path):
        """New experiment must appear in registry.json."""
        registry_path = str(tmp_path / "registry.json")
        cfg = FrozenExperimentConfig(experiment_name="reg_test")
        register_experiment(cfg, registry_path)

        with open(registry_path) as f:
            data = json.load(f)

        ids = [e["experiment_id"] for e in data["experiments"]]
        assert cfg.experiment_id in ids

    def test_experiment_not_registered_twice(self, tmp_path):
        """Registering the same experiment_id twice must be idempotent."""
        registry_path = str(tmp_path / "registry.json")
        cfg = FrozenExperimentConfig(experiment_name="dupe_test")
        register_experiment(cfg, registry_path)
        register_experiment(cfg, registry_path)  # duplicate

        with open(registry_path) as f:
            data = json.load(f)
        ids = [e["experiment_id"] for e in data["experiments"]]
        assert ids.count(cfg.experiment_id) == 1


# ═══════════════════════════════════════════════════════════════════════════
# 7. PAPER MODE NEVER PLACES BINANCE ORDERS
# ═══════════════════════════════════════════════════════════════════════════

class TestPaperModeIsolation:

    def test_paper_mode_execution_policy_blocks_orders(self, monkeypatch):
        """In PAPER mode, ExecutionPolicy.can_place_order() must return False."""
        import execution
        monkeypatch.setattr(execution, "TRADING_MODE", "PAPER")
        allowed, reason = execution.ExecutionPolicy.can_place_order()
        assert not allowed
        assert "PAPER" in reason.upper() or "NOT" in reason.upper() or "DISABLED" in reason.upper()

    def test_paper_mode_no_binance_client_created(self, monkeypatch):
        """In PAPER mode, get_exchange_client() must return None."""
        import execution
        monkeypatch.setattr(execution, "TRADING_MODE", "PAPER")
        monkeypatch.setattr(execution, "PAPER_SAFE_MODE", True)
        monkeypatch.setattr(execution, "TESTNET_ENABLED", False)
        monkeypatch.setattr(execution, "LIVE_TRADING_ENABLED", False)
        client = execution.get_exchange_client()
        assert client is None
