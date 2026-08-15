# Phase 13–15 Correction Pass

**Status: COMPLETE**
**Date:** 2026-08-15
**Commit:** (see git log)

---

## Corrections Made

### 1. Monte Carlo Benchmark (`paper_engine/benchmark.py`) — FIXED

**Problem:** The original benchmark used `trade_return -= 0.002`, a hardcoded magic number unrelated to the actual CostEngine.

**Fix:** `BenchmarkComparators.random_entry_monte_carlo()` now:
- Accepts an explicit `CostEngine` parameter (required).
- Applies `entry_fee`, `exit_fee`, `entry_slip`, `exit_slip`, `spread` on actual notional per trade.
- Supports explicit `random_seed` parameter — results are deterministic and reproducible.
- Reports `median_pnl`, `mean_pnl`, `p05_pnl`, `p95_pnl`, `fraction_beating_strategy`.
- For pairs strategies: `pairs_random_entry_monte_carlo()` models **both Leg A and Leg B** costs independently.
- For funding arbitrage: `funding_arb_random_monte_carlo()` models spot + perp costs + funding payments.

**Tests added:** 8 tests in `TestMonteCarloBenchmark`, 3 in `TestPairsBenchmark`, 2 in `TestFundingArbBenchmark`.

---

### 2. Kill Switch Accounting (`paper_engine/kill_switch.py`) — FIXED

**Problem:** The original kill switch closed positions with `exit_fee=0.0`, making forced exits appear profitable or neutral when they weren't.

**Fix:** `trigger_kill_switch()` now:
- Accepts `current_market_prices` and `cost_engine` parameters.
- Applies `exit_fee`, `exit_slip`, `spread` via the provided CostEngine on each forced exit.
- Annotates the ledger with a `KILL_SWITCH_ANNOTATION` record including the trigger reason.
- Falls back gracefully when market prices are unavailable (warns, uses entry price).
- Returns a summary dict with `total_exit_cost`, `total_exit_pnl`, `positions_closed`.
- Does NOT call `sys.exit()` — caller decides on process termination.

**Tests added:** 4 tests in `TestKillSwitchCosts`.

---

### 3. Software vs Economic Validation Separation — FIXED

**Problem:** The Phase 15 final report implicitly treated passing tests as evidence of trading profitability.

**Fix:** All documentation now uses an explicit validation hierarchy:

| Layer | Proves | Does NOT Prove |
|---|---|---|
| Software tests (pytest) | System reliability | Profitability |
| Historical backtests | Historical fit | Future profitability |
| Historical held-out data | No gross overfitting | Future profitability |
| Genuine wall-clock PAPER | Real market response | Future profitability |

Phase 14 is now explicitly labelled **SIMULATED FORWARD VALIDATION — NOT REAL WALL-CLOCK FORWARD VALIDATION**.

---

### 4. Statistical Validation (`paper_engine/statistical_report.py`) — ADDED

**Problem:** Statistical claims were implicit or fabricated.

**Fix:** Every significance claim now includes:
- Hypothesis H0 and H1 (explicit)
- Observation unit (per-trade net return fraction)
- Sample size
- Test name (one-sample t-test, one-tailed)
- t-statistic and p-value
- 95% confidence interval
- Results with `< 30 trades` always return `INCONCLUSIVE`
- Sharpe/Sortino only reported when `n ≥ 50`

**Tests added:** 5 tests in `TestStatisticalReport`.

---

### 5. Frozen Experiment Configuration (`paper_engine/experiment_config.py`) — ADDED

**Problem:** No mechanism prevented post-hoc parameter changes to contaminate forward experiment results.

**Fix:**
- `FrozenExperimentConfig` captures strategy params, CostEngine, risk limits, symbols, Git SHA at creation.
- Config is atomically saved to `experiments/<id>.json` — immutable.
- `mark_started()` raises if called twice.
- Duplicate registry entries are idempotent.
- Any parameter modification requires a NEW experiment ID.

**Tests added:** 5 tests in `TestFrozenExperimentConfig`.

---

### 6. PAPER Mode Isolation Tests — ADDED

**Tests added:** 2 tests in `TestPaperModeIsolation` proving:
- `ExecutionPolicy.can_place_order()` returns `(False, "PAPER_BLOCKED")` in PAPER mode.
- `get_exchange_client()` returns `None` in PAPER mode.

---

### 7. Documentation Updates

- `backtest_results/phase14/phase14_forward_validation_report.md` — rewritten with SIMULATED label, validation type table, corrected benchmark/kill-switch descriptions.
- `backtest_results/phase15/FINAL_REPORT.md` — rewritten with explicit statistical claims audit table, corrected deployment gate checklist (5/10 gates still unmet), removed all false economic claims.

---

## Forward Validation Experiment Definition

The genuine forward experiment is **NOT started**. When a human operator starts it:

```python
from paper_engine.experiment_config import create_experiment
from research_phase9.cost_engine import CostEngine

config = create_experiment(
    name="forward_exp_001",
    strategy_name="sma_crossover",  # frozen
    symbols=["BTCUSDT"],             # frozen
    strategy_params={"fast": 10, "slow": 30},  # frozen
    cost_engine=CostEngine.get_binance_taker_config(),  # frozen
    starting_capital=10000.0,
    planned_duration_days=30,
)
config.mark_started()  # captures start timestamp
config.save()
```

**Acceptance Criteria (Pre-registered, cannot change post-hoc):**

| Criterion | Required |
|---|---|
| Minimum trades | ≥ 30 |
| Net expectancy per trade | > 0% after all fees |
| Profit factor | ≥ 1.2 |
| Max drawdown | ≤ 20% |
| p-value (one-tailed t-test, H1: mean > 0) | < 0.05 |
| Beats random MC benchmark | Strategy > 50th percentile |
| Planned duration | ≥ 30 wall-clock days |

**PASS / FAIL / INCONCLUSIVE** is determined by `evaluate_against_acceptance_criteria()` after the experiment completes.
