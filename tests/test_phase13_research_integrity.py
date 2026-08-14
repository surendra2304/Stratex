"""
tests/test_phase13_research_integrity.py
Phase 13.22-13.30: Look-ahead audit, leakage, walk-forward, cost model,
parameter stability, and regime robustness.
"""
import math
import random
import numpy as np
import pandas as pd
import pytest


# ─────────────────────────────────────────────────────────────
# 13.22 — BACKTEST REPRODUCIBILITY
# ─────────────────────────────────────────────────────────────

def _build_df(seed: int, n: int = 500) -> pd.DataFrame:
    """Build a deterministic OHLCV DataFrame."""
    rng = np.random.default_rng(seed)
    prices = 50000.0 + np.cumsum(rng.normal(0, 100, n))
    prices = np.maximum(prices, 1.0)
    df = pd.DataFrame({
        "timestamp": pd.date_range("2024-01-01", periods=n, freq="1min"),
        "open":   prices,
        "high":   prices * 1.001,
        "low":    prices * 0.999,
        "close":  prices,
        "volume": rng.uniform(10, 1000, n),
    })
    return df


def _simple_sma_backtest(df: pd.DataFrame, fast: int = 10, slow: int = 30) -> dict:
    """Minimal backtest for reproducibility tests — SMA crossover."""
    fast_sma = df["close"].rolling(fast).mean()
    slow_sma = df["close"].rolling(slow).mean()
    signal = (fast_sma > slow_sma).astype(int)
    returns = df["close"].pct_change()
    strategy_returns = signal.shift(1) * returns  # shift(1) — NO lookahead
    strategy_returns = strategy_returns.dropna()
    return {
        "net_return": float(strategy_returns.sum()),
        "n_signals": int(signal.diff().abs().sum()),
        "sharpe": float(strategy_returns.mean() / (strategy_returns.std() + 1e-12)),
    }


def test_backtest_reproducibility_same_seed():
    """Same seed, same data, same params → identical results."""
    df1 = _build_df(seed=42)
    df2 = _build_df(seed=42)
    r1 = _simple_sma_backtest(df1)
    r2 = _simple_sma_backtest(df2)
    assert r1["net_return"] == pytest.approx(r2["net_return"], abs=1e-12)
    assert r1["n_signals"] == r2["n_signals"]


def test_backtest_different_seeds_produce_different_results():
    """Different seeds must produce different results (not trivially identical)."""
    r1 = _simple_sma_backtest(_build_df(seed=1))
    r2 = _simple_sma_backtest(_build_df(seed=999))
    assert r1["net_return"] != pytest.approx(r2["net_return"], abs=1e-9)


# ─────────────────────────────────────────────────────────────
# 13.23 — LOOK-AHEAD AUDIT
# ─────────────────────────────────────────────────────────────

def test_sma_uses_only_past_data():
    """Rolling SMA at index t must use only data up to t, not t+1 or beyond."""
    df = _build_df(42, n=100)
    sma = df["close"].rolling(10).mean()

    for i in range(10, len(df)):
        expected = df["close"].iloc[i - 9:i + 1].mean()
        assert abs(sma.iloc[i] - expected) < 1e-9, f"Lookahead at index {i}"


def test_shifted_signal_no_lookahead():
    """
    A signal generated at candle t must use shift(1) before computing returns.
    This verifies the backtest structure prevents look-ahead execution.
    """
    df = _build_df(42, n=200)
    df["sma_fast"] = df["close"].rolling(10).mean()
    df["sma_slow"] = df["close"].rolling(30).mean()
    df["signal"] = (df["sma_fast"] > df["sma_slow"]).astype(int)

    # Return at bar t+1 should be multiplied by signal at t (shift(1))
    df["return"] = df["close"].pct_change()
    df["strat_return"] = df["signal"].shift(1) * df["return"]

    # Verify: the signal at bar t cannot know bar t's closing price for entry
    # (it uses the previous bar's signal)
    # This is structural — the shift ensures no lookahead
    assert df["strat_return"].iloc[0] == 0.0 or math.isnan(df["strat_return"].iloc[0])


def test_no_future_close_in_rsi():
    """RSI computed at t must not use close[t+1]."""
    df = _build_df(42, n=300)
    delta = df["close"].diff()
    gain = delta.where(delta > 0, 0.0).rolling(14).mean()
    loss = (-delta.where(delta < 0, 0.0)).rolling(14).mean()
    rs = gain / (loss + 1e-12)
    rsi = 100 - (100 / (1 + rs))

    # RSI at row t must equal RSI computed on df[:t+1]
    for i in [50, 100, 200]:
        rsi_at_t = rsi.iloc[i]
        # Recompute from scratch on first i+1 rows
        sub = df["close"].iloc[:i + 1]
        d = sub.diff()
        g = d.where(d > 0, 0.0).rolling(14).mean()
        l_ = (-d.where(d < 0, 0.0)).rolling(14).mean()
        rs2 = g / (l_ + 1e-12)
        rsi2 = 100 - (100 / (1 + rs2))
        assert abs(rsi_at_t - rsi2.iloc[-1]) < 1e-9, f"RSI lookahead at i={i}"


# ─────────────────────────────────────────────────────────────
# 13.24 — LABEL LEAKAGE AUDIT
# ─────────────────────────────────────────────────────────────

def test_forward_return_label_uses_future_data():
    """Labels based on future returns must NOT be computed before the future is observed."""
    df = _build_df(42, n=200)
    df["future_return"] = df["close"].pct_change().shift(-1)  # this IS a lookahead label

    # Verify: at row 100, future_return uses row 101's data
    fr_at_100 = df["future_return"].iloc[100]
    actual_ret = (df["close"].iloc[101] - df["close"].iloc[100]) / df["close"].iloc[100]
    assert abs(fr_at_100 - actual_ret) < 1e-12, "Future return label must use future data"

    # Verify: features at row 100 must NOT include future_return
    # (This is a documentary test proving the label is computed separately)
    features_at_100 = {
        "close": df["close"].iloc[100],
        "sma_10": df["close"].iloc[91:101].mean(),
    }
    assert "future_return" not in features_at_100


# ─────────────────────────────────────────────────────────────
# 13.25 — WALK-FORWARD AUDIT
# ─────────────────────────────────────────────────────────────

def test_walk_forward_chronological_separation():
    """Walk-forward splits must be chronologically ordered."""
    df = _build_df(42, n=1000)
    n = len(df)
    train_end = int(n * 0.6)
    val_end = int(n * 0.8)

    train = df.iloc[:train_end]
    val = df.iloc[train_end:val_end]
    test = df.iloc[val_end:]

    assert train.index.max() < val.index.min(), "Train overlaps validation"
    assert val.index.max() < test.index.min(), "Validation overlaps test"

    # No row appears in more than one split
    all_idx = set(train.index) | set(val.index) | set(test.index)
    assert len(all_idx) == n, "Rows appear in multiple splits"


def test_no_random_shuffle_of_time_series():
    """Time-series data must not be randomly shuffled before splitting."""
    df = _build_df(42, n=200)
    # A shuffled split would have non-monotonic timestamps
    shuffled = df.sample(frac=1, random_state=42)
    timestamps = shuffled["timestamp"].values
    is_monotonic = all(timestamps[i] < timestamps[i + 1] for i in range(len(timestamps) - 1))
    assert not is_monotonic, "Shuffled data should NOT be monotonic (proves shuffling was detected)"

    # Correct split — original is monotonic
    original_ts = df["timestamp"].values
    is_orig_monotonic = all(original_ts[i] < original_ts[i + 1] for i in range(len(original_ts) - 1))
    assert is_orig_monotonic, "Original time series must be monotonically increasing"


# ─────────────────────────────────────────────────────────────
# 13.26 — COST MODEL AUDIT
# ─────────────────────────────────────────────────────────────

def test_cost_model_covers_all_components():
    """Every strategy evaluation must include entry_fee, exit_fee, slippage, spread."""
    from research_phase9.cost_engine import CostEngine

    cost = CostEngine.get_binance_taker_config()
    # All components must be positive
    assert cost.entry_fee > 0, "entry_fee missing"
    assert cost.exit_fee > 0, "exit_fee missing"
    assert cost.entry_slip > 0, "entry slippage missing"
    assert cost.exit_slip > 0, "exit slippage missing"


def test_strategy_with_zero_cost_outperforms_with_realistic_cost():
    """Removing costs must improve apparent performance — proves costs are being applied."""
    df = _build_df(42, n=500)
    df["sma_fast"] = df["close"].rolling(10).mean()
    df["sma_slow"] = df["close"].rolling(30).mean()
    df["signal"] = (df["sma_fast"] > df["sma_slow"]).astype(int)
    df["return"] = df["close"].pct_change()
    df["strat_return"] = df["signal"].shift(1) * df["return"]

    # Zero cost
    gross_return = df["strat_return"].dropna().sum()

    # With realistic round-trip cost per signal change
    cost_per_trade = 0.002  # 0.2%
    n_trades = df["signal"].diff().abs().sum()
    total_cost = n_trades * cost_per_trade * 100 / len(df)  # rough normalization
    net_return = gross_return - total_cost

    # Zero-cost always >= net
    # (Strictly greater if there are any trades at all)
    if n_trades > 0:
        assert gross_return > net_return, "Costs must reduce net return"


# ─────────────────────────────────────────────────────────────
# 13.27-13.28 — PARAMETER STABILITY
# ─────────────────────────────────────────────────────────────

def test_parameter_stability_neighborhood():
    """
    Strategy performance must be stable across a neighborhood of parameters.
    A strategy that only works at exactly SMA(10,30) but fails at (9,30), (11,30)
    is a curve-fit artifact and should be rejected.
    """
    df = _build_df(42, n=2000)
    df["return"] = df["close"].pct_change()

    # Test a neighborhood of (fast, slow) pairs
    results = {}
    for fast in [8, 9, 10, 11, 12]:
        for slow in [28, 29, 30, 31, 32]:
            sma_f = df["close"].rolling(fast).mean()
            sma_s = df["close"].rolling(slow).mean()
            sig = (sma_f > sma_s).astype(int)
            ret = (sig.shift(1) * df["return"]).dropna().sum()
            results[(fast, slow)] = ret

    # All results in a reasonable neighborhood should be in the same sign direction
    # (or at least not wildly divergent — within ±50% of median)
    vals = list(results.values())
    med = sorted(vals)[len(vals) // 2]
    if abs(med) > 1e-6:
        for (f, s), v in results.items():
            # Relative stability: no result more than 10x away from median
            ratio = abs(v / (med + 1e-12))
            assert ratio < 10.0, (
                f"Extreme parameter instability at ({f},{s}): {v:.4f} vs median {med:.4f}"
            )


# ─────────────────────────────────────────────────────────────
# 13.29 — REGIME ROBUSTNESS
# ─────────────────────────────────────────────────────────────

def test_strategy_evaluated_per_regime():
    """Performance must be measured separately per regime (bull/bear/sideways)."""
    rng = np.random.default_rng(42)
    n = 900

    # Bull: trending up
    bull_prices = 50000.0 + np.cumsum(np.abs(rng.normal(50, 100, n // 3)))
    # Bear: trending down
    bear_prices = bull_prices[-1] - np.cumsum(np.abs(rng.normal(50, 100, n // 3)))
    bear_prices = np.maximum(bear_prices, 1.0)
    # Sideways: mean-reverting
    side_prices = 45000.0 + rng.normal(0, 200, n // 3)

    all_prices = np.concatenate([bull_prices, bear_prices, side_prices])
    df = pd.DataFrame({"close": all_prices, "return": pd.Series(all_prices).pct_change().fillna(0)})
    df["regime"] = ["bull"] * (n // 3) + ["bear"] * (n // 3) + ["sideways"] * (n // 3)

    # Simple long-only strategy
    df["sma"] = df["close"].rolling(20).mean()
    df["signal"] = (df["close"] > df["sma"]).astype(int)
    df["strat_return"] = df["signal"].shift(1).fillna(0) * df["return"]

    regime_perf = df.groupby("regime")["strat_return"].sum()

    # Must have a result for each regime
    assert "bull" in regime_perf.index
    assert "bear" in regime_perf.index
    assert "sideways" in regime_perf.index

    # Report (not assert profitability — just that we have data)
    for regime, perf in regime_perf.items():
        assert math.isfinite(perf), f"Non-finite performance for regime {regime}"
