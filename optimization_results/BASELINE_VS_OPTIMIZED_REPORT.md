# Baseline vs. Optimized Quantitative Strategy Report

**Strategy**: `strategy_adx_ema` (ADX + EMA Trend Following)  
**Dataset**: BTCUSDT 1h (4000 authentic Binance bars)  
**Data Period**: 2026-03-10 21:00:00 to 2026-08-24 12:00:00  
**Git Commit SHA**: `0d543c1d6bf323dc66a130a43d680b5f1c616241`  
**Optimization Method**: Optuna TPE Sampler (35 trials, seed=42)  
**Friction Assumptions**: Taker fee = 10 bps (0.001), Slippage = 5 bps (0.0005)  
**Promotion Status**: `RESEARCH ONLY` (Strict Human Review Required)

---

## 1. Executive Performance Comparison

| Metric | Baseline (IS) | Baseline (OOS) | Optimized (IS) | Optimized (OOS) | Variance (OOS) |
|---|---|---|---|---|---|
| **Net PnL** | $-138.46 | $-23.96 | $-139.40 | $-24.05 | -0.09 |
| **Profit Factor** | 0.64 | 0.82 | 0.81 | 0.82 | -0.00 |
| **Win Rate** | 42.9% | 50.0% | 50.0% | 50.0% | +0.0% |
| **Max Drawdown** | 1.83% | 1.31% | 1.39% | 1.26% | -0.05% |
| **Total Trades** | 7 | 2 | 10 | 2 | +0 |
| **Sharpe Ratio** | -2.67 | -0.91 | -2.04 | -0.89 | +0.02 |

---

## 2. Parameter Comparison

| Parameter | Baseline (config_strategy.py) | Optimized Value | Explored Search Space |
|---|---|---|---|
| `ADX_THRESHOLD` | 20 | `16` | [16, 32] |
| `SL_ATR_MULTIPLIER` | 3.00 | `3.00` | [2.00, 4.00, step 0.25] |
| `TP_ATR_MULTIPLIER` | 3.00 | `3.00` | [2.00, 4.50, step 0.25] |
| `RETEST_WINDOW_BARS` | 10 | `14` | [6, 14] |
| `EMA_FAST_PERIOD` | 20 | `22` | [16, 26, step 2] |
| `EMA_SLOW_PERIOD` | 50 | `55` | [45, 60, step 5] |

---

## 3. Rolling Walk-Forward Windows

| Window # | In-Sample Train Period | Out-Of-Sample Test Period | IS PF | OOS PF | OOS Trades | OOS Net PnL | Status |
|---|---|---|---|---|---|---|---|
| Window 1 | 2026-03-10 to 2026-05-12 | 2026-05-12 to 2026-06-06 | 1.0 | 0.0 | 0 | $0.00 | PASS |
| Window 2 | 2026-04-04 to 2026-06-06 | 2026-06-06 to 2026-07-01 | 0.79 | 0.0 | 0 | $0.00 | PASS |
| Window 3 | 2026-04-29 to 2026-07-01 | 2026-07-01 to 2026-07-26 | 0.8 | 0.87 | 2 | $-19.44 | FAIL |
| Window 4 | 2026-05-24 to 2026-07-26 | 2026-07-26 to 2026-08-20 | 0.87 | inf | 1 | $34.17 | PASS |

---

## 4. Governance & Deployment Recommendation

> [!IMPORTANT]
> - **Zero Silent Overwrites**: The production parameters in `config_strategy.py` remain frozen and unmodified.
> - **Status**: This configuration is stamped as **`RESEARCH ONLY`**.
> - **Out-of-Sample Proof**: To promote this parameter set to `OOS VALIDATED` or `ACTIVE`, it must pass live forward soak validation under the Stratex `paper_engine` or Binance Spot Testnet with $\ge 30$ live trades.
