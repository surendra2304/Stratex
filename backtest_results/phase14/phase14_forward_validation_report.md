# Phase 14: Forward Validation Framework
## ⚠️ SIMULATED FORWARD VALIDATION — NOT REAL WALL-CLOCK FORWARD VALIDATION

> [!WARNING]
> The session labelled "simulated forward validation" in this phase used **held-out historical data**, not genuine future market data. Historical held-out data is NOT equivalent to genuine forward validation. Positive PnL on held-out historical data proves only that the strategy had a plausible historical fit on that particular sub-sample. It does **NOT** prove economic edge on future, unseen data.

---

## What Each Validation Type Proves

| Validation Type | Evidence Provided | NOT Proved |
|---|---|---|
| **165/165 software tests pass** | System is reliable and correct under known conditions | Profitability |
| **Historical backtest** | Strategy had a historical fit on specific in-sample data | Future profitability |
| **Held-out historical data (simulated OOS)** | Strategy was not overfit to specific in-sample dates | Future profitability |
| **Genuine wall-clock PAPER trading** | Strategy responded correctly to real market microstructure | Future profitability |
| **Profitable genuine PAPER trading** | Minimum necessary (not sufficient) condition for deployment | Guaranteed future profit |

---

## 1. Deployed Infrastructure

All components listed below are installed and tested. They are NOT running in real-time until the genuine forward experiment is explicitly started by a human operator.

### `ForwardValidator` (`paper_engine/forward_validator.py`)
- Chronological row-by-row iteration engine.
- Enforces zero-lookahead via `df.iloc[:idx+1]` window slicing.

### `BenchmarkComparators` (`paper_engine/benchmark.py`)
- **Corrected:** Monte Carlo now uses the **same CostEngine** as the actual strategy.
- Applies entry_fee, exit_fee, entry_slip, exit_slip, spread, and spread on actual notional.
- Two-leg pairs: models **both Leg A and Leg B** independently.
- Funding arbitrage: models **spot + perp + funding payments**.
- **Reproducible:** explicit `random_seed` parameter required; results are deterministic.
- Reports: `median_pnl`, `p05_pnl`, `p95_pnl`, `fraction_beating_strategy`.

### `KillSwitch` (`paper_engine/kill_switch.py`)
- **Corrected:** Kill switch NEVER closes positions at zero cost.
- All forced exits apply: `exit_fee + exit_slip + spread` via CostEngine.
- Reason `KILL_SWITCH` is annotated in the ledger for audit.
- Lock file is written atomically before any position is touched.

### `FrozenExperimentConfig` (`paper_engine/experiment_config.py`)
- Configuration is captured once and saved atomically to `experiments/<id>.json`.
- Git SHA is captured at creation time.
- Pre-registered acceptance criteria cannot be changed after start.
- Duplicate registration is idempotent.

### `StatisticalReport` (`paper_engine/statistical_report.py`)
- Every significance claim includes: H0, H1, test name, statistic, sample size, p-value, CI.
- Results with `< 30 trades` are always labelled **INCONCLUSIVE**.
- Multiple comparison corrections noted in report header.
- `PASS` on statistical tests means only that the pre-defined criteria were met. It does **not** mean the strategy will be profitable in the future.

### `DailyReportGenerator` (`paper_engine/daily_report.py`)
- Generates standardized daily session reports.

### `kill_switch.py`
- Emergency halt. Flattens positions with realistic costs. Annotates ledger.

---

## 2. Forward Validation Acceptance Criteria (Pre-Registered)

The following criteria are frozen before the experiment starts. They CANNOT be changed post-hoc without starting a NEW experiment.

| Criterion | Required Value | Notes |
|---|---|---|
| Minimum trades | ≥ 30 | Below this, all results are INCONCLUSIVE |
| Expectancy per trade | > 0% (after all fees) | Primary economic gate |
| Profit factor | ≥ 1.2 | Gross profit / Gross loss |
| Max drawdown | ≤ 20% | Over the forward period |
| Statistical significance | p < 0.05 (one-tailed t-test) | H0: mean_return ≤ 0 |
| Beats random benchmark | Strategy in > 50th percentile of MC | Uses same CostEngine |
| Sharpe ratio | ≥ 0.5 | Only reported if n ≥ 50 trades |
| Planned duration | 30 days | Minimum wall-clock time |

---

## 3. Genuine Forward Experiment Definition

The genuine forward experiment has **NOT been started**. When a human operator explicitly starts it:

1. A `FrozenExperimentConfig` is created with Git SHA, strategy params, CostEngine params, and symbols.
2. The config is saved to `experiments/<id>.json` and registered in `experiments/registry.json`.
3. `config.mark_started()` is called — this timestamp is the official experiment start.
4. The system runs in `PAPER` mode only (zero Binance orders placed).
5. Signals, trades, and equity snapshots are appended to append-only files.
6. After ≥30 days and ≥30 trades, `evaluate_against_acceptance_criteria()` is called.

---

## 4. Current Status

**Classification: `INFRASTRUCTURE COMPLETE — GENUINE FORWARD EXPERIMENT NOT STARTED`**

Live trading remains **BLOCKED**. No orders of any kind have been placed on Testnet or Live.
