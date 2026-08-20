# QUANTUM BENCHMARK REPORT — WALK-FORWARD PROFITABILITY VALIDATION

**Execution Timestamp:** 2026-08-20 10:45:32 UTC  
**Benchmark Engine:** `quantum/validation`  
**Benchmark Execution Time:** 604.51 seconds  
**Production Trading Authority:** ZERO (RESEARCH / ADVISORY ONLY)  

---

## 1. EXECUTIVE SUMMARY & VERDICT

```
================================================================================
QUANTUM VERDICT: C — INSUFFICIENT EVIDENCE
================================================================================
```

**Verdict Rationale:** Historical data span (8.83 days) is less than the strict 90-day calendar requirement for full 60d/15d/15d splits. Evaluated on proportional rolling partitions.

---

## 2. DATASET AUDIT & PROVENANCE

| Parameter | Measured Value |
| :--- | :--- |
| **Symbol** | `BTCUSDT` |
| **Timeframe** | `1m` |
| **File Path** | `data_cache\BTCUSDT_1m_90d.parquet` |
| **Row Count** | 12715 candles |
| **Start Timestamp** | `2026-08-05 18:50:00` |
| **End Timestamp** | `2026-08-14 14:44:00` |
| **Calendar Span** | 8.83 days |
| **Missing Data Intervals** | 0 |
| **Duplicate Timestamps** | 0 |
| **Sufficient for 90-Day Calendar Splits?** | `NO (INSUFFICIENT_SPAN_8.8D_OF_90D_REQ)` |

---

## 3. WALK-FORWARD METHODOLOGY

The benchmark partitioned historical market data into **5 chronological walk-forward folds**. For each fold:
1. Feature scaling, parameters, and ML classifiers were fitted **strictly on the Train slice**.
2. Hyperparameters were checked against the **Validation slice**.
3. Final execution was evaluated **once on the out-of-sample Test slice**.
4. Standard Binance fee (0.10%) and execution slippage (0.05%) were applied to all fills.

| Fold | Train Range | Val Range | Test Range | Train Rows | Val Rows | Test Rows |
| :---: | :--- | :--- | :--- | :---: | :---: | :---: |
| **Fold 1** | `2026-08-05 → 2026-08-09` | `2026-08-09 → 2026-08-10` | `2026-08-10 → 2026-08-11` | 5725 | 1271 | 1907 |
| **Fold 2** | `2026-08-06 → 2026-08-10` | `2026-08-10 → 2026-08-11` | `2026-08-11 → 2026-08-12` | 6357 | 1271 | 1907 |
| **Fold 3** | `2026-08-06 → 2026-08-11` | `2026-08-11 → 2026-08-11` | `2026-08-11 → 2026-08-13` | 6357 | 1271 | 1907 |
| **Fold 4** | `2026-08-07 → 2026-08-11` | `2026-08-11 → 2026-08-12` | `2026-08-12 → 2026-08-13` | 6357 | 1271 | 1907 |
| **Fold 5** | `2026-08-07 → 2026-08-12` | `2026-08-12 → 2026-08-13` | `2026-08-13 → 2026-08-14` | 6357 | 1271 | 1907 |

---

## 4. AGGREGATE PERFORMANCE COMPARISON

| Method | Backend Used | Trades | Win Rate | Profit Factor | Net P&L ($) | Net Return (%) | Max DD (%) | Avg P&L ($) | Total Fees ($) | Latency (ms) |
| :--- | :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| **Classical_Rule_Based** | `Local_CPU` | 1690 | 0.71% | 0.01 | $-31903.51 | -63.81% | 63.87% | $-18.88 | $21207.72 | 13.82 ms |
| **Classical_ML_Baseline** | `Local_CPU` | 7 | 14.29% | 0.11 | $-312.36 | -0.62% | 0.7% | $-44.62 | $123.57 | 15.86 ms |
| **Pure_Quantum_VQC** | `Classical_VQC_Simulator` | 608 | 0.66% | 0.0 | $-14961.36 | -29.92% | 29.92% | $-24.61 | $9911.44 | 15.53 ms |
| **Hybrid_Quantum_Classical** | `Classical_VQC_Simulator` | 5 | 20.0% | 0.12 | $-294.59 | -0.59% | 0.67% | $-58.92 | $83.75 | 15.79 ms |
| **Quantum_Portfolio_Optimizer** | `QUBO_Exact_Statevector_Fallback` | 1690 | 0.71% | 0.01 | $-31903.51 | -63.81% | 63.87% | $-18.88 | $21207.72 | 12.69 ms |

---

## 5. FOLD-BY-FOLD DETAILED BREAKDOWN

### Fold 1 (Test Period: `2026-08-10 15:26:00` to `2026-08-11 23:12:00`)

| Strategy | Trades | Win Rate | Profit Factor | Net P&L ($) | Net Return (%) | Max DD (%) | Avg Trade ($) | Total Fees ($) |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| **Classical_Rule_Based** | 348 | 0.29% | 0.0 | $-6488.82 | -64.89% | 64.89% | $-18.65 | $4298.54 |
| **Classical_ML_Baseline** | 1 | 100.0% | 99.0 | $38.94 | 0.39% | 0.00% | $38.94 | $20.06 |
| **Pure_Quantum_VQC** | 68 | 1.47% | 0.01 | $-1859.30 | -18.59% | 18.59% | $-27.34 | $1229.43 |
| **Hybrid_Quantum_Classical** | 1 | 100.0% | 99.0 | $38.94 | 0.39% | 0.00% | $38.94 | $20.06 |
| **Quantum_Portfolio_Optimizer** | 348 | 0.29% | 0.0 | $-6488.82 | -64.89% | 64.89% | $-18.65 | $4298.54 |

### Fold 2 (Test Period: `2026-08-11 07:19:00` to `2026-08-12 15:05:00`)

| Strategy | Trades | Win Rate | Profit Factor | Net P&L ($) | Net Return (%) | Max DD (%) | Avg Trade ($) | Total Fees ($) |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| **Classical_Rule_Based** | 333 | 1.2% | 0.01 | $-6257.81 | -62.58% | 62.86% | $-18.79 | $4235.70 |
| **Classical_ML_Baseline** | 1 | 0.0% | 0.0 | $-114.82 | -1.15% | 1.15% | $-114.82 | $11.88 |
| **Pure_Quantum_VQC** | 93 | 0.0% | 0.0 | $-2535.85 | -25.36% | 25.36% | $-27.27 | $1612.89 |
| **Hybrid_Quantum_Classical** | 1 | 0.0% | 0.0 | $-114.82 | -1.15% | 1.15% | $-114.82 | $11.88 |
| **Quantum_Portfolio_Optimizer** | 333 | 1.2% | 0.01 | $-6257.81 | -62.58% | 62.86% | $-18.79 | $4235.70 |

### Fold 3 (Test Period: `2026-08-11 23:12:00` to `2026-08-13 06:58:00`)

| Strategy | Trades | Win Rate | Profit Factor | Net P&L ($) | Net Return (%) | Max DD (%) | Avg Trade ($) | Total Fees ($) |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| **Classical_Rule_Based** | 338 | 1.18% | 0.02 | $-6364.83 | -63.65% | 63.65% | $-18.83 | $4238.39 |
| **Classical_ML_Baseline** | 1 | 0.0% | 0.0 | $-114.82 | -1.15% | 1.15% | $-114.82 | $11.88 |
| **Pure_Quantum_VQC** | 84 | 0.0% | 0.0 | $-2281.22 | -22.81% | 22.81% | $-27.16 | $1468.70 |
| **Hybrid_Quantum_Classical** | 1 | 0.0% | 0.0 | $-114.82 | -1.15% | 1.15% | $-114.82 | $11.88 |
| **Quantum_Portfolio_Optimizer** | 338 | 1.18% | 0.02 | $-6364.83 | -63.65% | 63.65% | $-18.83 | $4238.39 |

### Fold 4 (Test Period: `2026-08-12 15:05:00` to `2026-08-13 22:51:00`)

| Strategy | Trades | Win Rate | Profit Factor | Net P&L ($) | Net Return (%) | Max DD (%) | Avg Trade ($) | Total Fees ($) |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| **Classical_Rule_Based** | 331 | 0.6% | 0.01 | $-6343.61 | -63.44% | 63.45% | $-19.16 | $4182.02 |
| **Classical_ML_Baseline** | 2 | 0.0% | 0.0 | $-60.83 | -0.61% | 0.61% | $-30.41 | $39.88 |
| **Pure_Quantum_VQC** | 158 | 1.27% | 0.0 | $-3726.43 | -37.26% | 37.26% | $-23.59 | $2525.16 |
| **Hybrid_Quantum_Classical** | 1 | 0.0% | 0.0 | $-51.94 | -0.52% | 0.52% | $-51.94 | $19.97 |
| **Quantum_Portfolio_Optimizer** | 331 | 0.6% | 0.01 | $-6343.61 | -63.44% | 63.45% | $-19.16 | $4182.02 |

### Fold 5 (Test Period: `2026-08-13 06:58:00` to `2026-08-14 14:44:00`)

| Strategy | Trades | Win Rate | Profit Factor | Net P&L ($) | Net Return (%) | Max DD (%) | Avg Trade ($) | Total Fees ($) |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| **Classical_Rule_Based** | 340 | 0.29% | 0.01 | $-6448.44 | -64.48% | 64.48% | $-18.97 | $4253.06 |
| **Classical_ML_Baseline** | 2 | 0.0% | 0.0 | $-60.83 | -0.61% | 0.61% | $-30.41 | $39.88 |
| **Pure_Quantum_VQC** | 205 | 0.49% | 0.0 | $-4558.55 | -45.59% | 45.59% | $-22.24 | $3075.25 |
| **Hybrid_Quantum_Classical** | 1 | 0.0% | 0.0 | $-51.94 | -0.52% | 0.52% | $-51.94 | $19.97 |
| **Quantum_Portfolio_Optimizer** | 340 | 0.29% | 0.01 | $-6448.44 | -64.48% | 64.48% | $-18.97 | $4253.06 |

---

## 6. BOOTSTRAP STATISTICAL VALIDATION (10,000 RESAMPLES)

Statistical hypothesis testing was conducted using 10,000 bootstrap resamplings of out-of-sample trade return differentials.

| Comparison Hypothesis | Sample Sizes (A vs B) | Mean Difference (%) | 95% Two-Sided CI | Empirical p-value | Entirely Positive? | Significant (p<0.05)? |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: |
| **Pure_VQC_vs_Classical_Rule** | 608 vs 1690 | +0.0025% | `[-0.0073%, +0.0127%]` | 0.6324 | `NO` | `NO` |
| **Hybrid_vs_Classical_ML** | 5 vs 7 | -0.2285% | `[-1.1874%, +0.7615%]` | 0.6530 | `NO` | `NO` |
| **Optimizer_vs_Classical_Rule** | 1690 vs 1690 | +0.0000% | `[+0.0000%, +0.0000%]` | 1.0000 | `NO` | `NO` |

---

## 7. SIMULATOR VS. PHYSICAL QUANTUM HARDWARE

* **Physical QPU Hardware Used:** **0.0 seconds** (No real quantum hardware was accessed during this benchmark).
* **Simulation Infrastructure:** Classical CPU simulation (`Statevector Unitary & Angle Embeddings`).
* **Distinction:** All metrics reflect classical simulation of shallow quantum circuits; no quantum speedup or physical hardware advantage is claimed.

---

## 8. DATA LEAKAGE AUDIT

1. **Feature Construction:** All technical indicators in `features.py` use strictly backward-looking windows.
2. **Standardization & Preprocessing:** Scalers (`StandardScaler`) are fit exclusively on the `train_df` of each fold.
3. **Model Selection & Freezing:** Models are frozen prior to out-of-sample evaluation on `test_df`.
4. **Anti-Lookahead Verification:** Verified zero usage of future candles (`shift(-1)`) or forward labels.

---

## 9. CONCLUSION

**Final Verdict:** **C — INSUFFICIENT EVIDENCE**  
**Operational Status:** **RESEARCH / ADVISORY ONLY**  
The quantum research subsystem remains completely isolated from the live/testnet trading execution engine.
