# PROFITABILITY FORENSIC VALIDATION REPORT

**Execution Timestamp:** 2026-08-21 11:00:00 UTC  
**Audit Scope:** Complete Forensic Verification, Data Provenance, ML Leakage Audit, and Statistical De-Fabrication  
**Trading Status:** TESTNET / PAPER ONLY (Live Trading Permanently Locked: LIVE_TRADING_ENABLED = False)  

---

## 1. EXECUTIVE VERDICT & INVALIDATED CLAIMS SUMMARY

`
================================================================================
FINAL FORENSIC SCIENTIFIC VERDICT:
B — NO STATISTICALLY SIGNIFICANT OUT-OF-SAMPLE PROFITABILITY DETECTED
================================================================================
`

### Forensic Inventory of Invalidated Claims:
1. **Invalidated Claim 1 — ML +.19 on LINKUSDT 1h (INVALIDATED / IN-SAMPLE ARTIFACT):**  
   - **Investigation:** Backtested strategy_ml.py on data_cache/LINKUSDT_1h.csv using the serialized model_buy.pkl from disk. While running the pre-trained weights on the full file returned +.19 over 9 trades, retraining the model strictly on the chronological Train slice (first 50%) and evaluating strictly out-of-sample on the Test slice produced **0 trades** and **.00 PnL**. The serialized weights were fitted in-sample without walk-forward separation.
2. **Invalidated Claim 2 — Extreme Profit Factors (~99.0) (INVALIDATED / ZERO-LOSS ARTIFACT):**  
   - **Investigation:** Code audit revealed that when a strategy produces 1 or 2 trades that happen to hit Take Profit without any losses in a micro-sample, the code substituted 99.0 for gross_profit / 0.0. In reality, PF = UNDEFINED / INFINITE due to small sample size ( \le 4$), not genuine infinite edge.
3. **Invalidated Claim 3 — 425 / 641 / 182 / 48 Counts Discrepancy (INVALIDATED):**  
   - **Investigation:** These counts represented transient in-memory evaluations from an ad-hoc session script combining different subsets of bars without persistent provenance. They are superseded by the authoritative 36-dataset evaluation matrix below.
4. **Invalidated Claim 4 — Out-of-Sample Alpha on Sub-15m Timeframes (INVALIDATED):**  
   - **Investigation:** Strict chronological walk-forward testing across 12,715 candles of BTCUSDT 1m produced 87 trades with **-2,526.19 USDT Net PnL**, a **1.15% win rate**, and **0.02 Profit Factor**. Retail taker friction (0.31% roundtrip) mathematically consumes all micro-structure edge on $\le 15	ext{m}$ intervals.

---

## 2. DATASET PROVENANCE INVENTORY

Authoritative inventory of all 36 cached historical datasets in data_cache/:

| Dataset Filename | Timeframe | Rows | File Size (Bytes) | First Timestamp | Last Timestamp | Span |
| :--- | :---: | :---: | :---: | :--- | :--- | :---: |
| ADAUSDT_1m.csv | 1m | 1,000 | 83,234 | 2026-08-15 17:25:00 | 2026-08-16 10:04:00 | 0.70 days |
| ADAUSDT_15m.csv | 15m | 1,000 | 92,702 | 2026-08-06 00:15:00 | 2026-08-16 10:00:00 | 10.41 days |
| ADAUSDT_1h.csv | 1h | 257 | 24,523 | 2026-08-05 18:00:00 | 2026-08-16 10:00:00 | 10.67 days |
| ADAUSDT_4h.csv | 4h | 65 | 6,410 | 2026-08-05 16:00:00 | 2026-08-16 08:00:00 | 10.67 days |
| ADAUSDT_1d.csv | 1d | 12 | 1,152 | 2026-08-05 | 2026-08-16 | 11.00 days |
| BNBUSDT_1m.csv | 1m | 1,000 | 82,470 | 2026-08-15 17:25:00 | 2026-08-16 10:04:00 | 0.70 days |
| BNBUSDT_15m.csv | 15m | 1,000 | 90,227 | 2026-08-06 00:15:00 | 2026-08-16 10:00:00 | 10.41 days |
| BNBUSDT_1h.csv | 1h | 257 | 23,840 | 2026-08-05 18:00:00 | 2026-08-16 10:00:00 | 10.67 days |
| BNBUSDT_4h.csv | 4h | 65 | 6,185 | 2026-08-05 16:00:00 | 2026-08-16 08:00:00 | 10.67 days |
| BNBUSDT_1d.csv | 1d | 12 | 1,137 | 2026-08-05 | 2026-08-16 | 11.00 days |
| BNBUSDT_1m_90d.parquet | 1m | 12,738 | 355,054 | 2026-08-05 18:50:00 | 2026-08-14 15:07:00 | 8.85 days |
| BTCUSDT_1m.csv | 1m | 1,000 | 104,784 | 2026-08-15 17:25:00 | 2026-08-16 10:04:00 | 0.70 days |
| BTCUSDT_15m.csv | 15m | 1,000 | 103,557 | 2026-08-06 00:15:00 | 2026-08-16 10:00:00 | 10.41 days |
| BTCUSDT_1h.csv | 1h | 257 | 27,039 | 2026-08-05 18:00:00 | 2026-08-16 10:00:00 | 10.67 days |
| BTCUSDT_4h.csv | 4h | 65 | 7,057 | 2026-08-05 16:00:00 | 2026-08-16 08:00:00 | 10.67 days |
| BTCUSDT_1d.csv | 1d | 12 | 1,287 | 2026-08-05 | 2026-08-16 | 11.00 days |
| BTCUSDT_1m_30d.parquet | 1m | 12,733 | 873,508 | 2026-08-05 18:50:00 | 2026-08-14 15:02:00 | 8.84 days |
| BTCUSDT_1m_90d.parquet | 1m | 12,715 | 872,101 | 2026-08-05 18:50:00 | 2026-08-14 14:44:00 | 8.83 days |
| ETHUSDT_1m.csv | 1m | 1,000 | 97,798 | 2026-08-15 17:25:00 | 2026-08-16 10:04:00 | 0.70 days |
| ETHUSDT_15m.csv | 15m | 1,000 | 99,347 | 2026-08-06 00:15:00 | 2026-08-16 10:00:00 | 10.41 days |
| ETHUSDT_1h.csv | 1h | 257 | 25,986 | 2026-08-05 18:00:00 | 2026-08-16 10:00:00 | 10.67 days |
| ETHUSDT_4h.csv | 4h | 65 | 6,750 | 2026-08-05 16:00:00 | 2026-08-16 08:00:00 | 10.67 days |
| ETHUSDT_1d.csv | 1d | 12 | 1,178 | 2026-08-05 | 2026-08-16 | 11.00 days |
| ETHUSDT_1m_30d.parquet | 1m | 12,733 | 672,040 | 2026-08-05 18:50:00 | 2026-08-14 15:02:00 | 8.84 days |
| ETHUSDT_1m_90d.parquet | 1m | 12,738 | 672,346 | 2026-08-05 18:50:00 | 2026-08-14 15:07:00 | 8.85 days |
| LINKUSDT_1m.csv | 1m | 1,000 | 83,121 | 2026-08-15 17:25:00 | 2026-08-16 10:04:00 | 0.70 days |
| LINKUSDT_15m.csv | 15m | 1,000 | 88,685 | 2026-08-06 00:15:00 | 2026-08-16 10:00:00 | 10.41 days |
| LINKUSDT_1h.csv | 1h | 257 | 23,392 | 2026-08-05 18:00:00 | 2026-08-16 10:00:00 | 10.67 days |
| LINKUSDT_4h.csv | 4h | 65 | 5,847 | 2026-08-05 16:00:00 | 2026-08-16 08:00:00 | 10.67 days |
| LINKUSDT_1d.csv | 1d | 12 | 1,081 | 2026-08-05 | 2026-08-16 | 11.00 days |
| SOLUSDT_1m.csv | 1m | 1,000 | 87,742 | 2026-08-15 17:25:00 | 2026-08-16 10:04:00 | 0.70 days |
| SOLUSDT_15m.csv | 15m | 1,000 | 91,280 | 2026-08-06 00:15:00 | 2026-08-16 10:00:00 | 10.41 days |
| SOLUSDT_1h.csv | 1h | 257 | 24,059 | 2026-08-05 18:00:00 | 2026-08-16 10:00:00 | 10.67 days |
| SOLUSDT_4h.csv | 4h | 65 | 6,291 | 2026-08-05 16:00:00 | 2026-08-16 08:00:00 | 10.67 days |
| SOLUSDT_1d.csv | 1d | 12 | 1,103 | 2026-08-05 | 2026-08-16 | 11.00 days |
| SOLUSDT_1m_90d.parquet | 1m | 12,739 | 581,464 | 2026-08-05 18:50:00 | 2026-08-14 15:08:00 | 8.85 days |

---

## 3. ACTUAL SYSTEM BACKTEST & EXECUTION INTEGRITY

### Code Accounting Audit:
- **Execution Price:** Next bar open $	imes (1 \pm 	ext{slippage})$, strictly causal.
- **Entry Fee:** Deducted from balance upon execution ($	ext{entry\_price} 	imes 	ext{qty} 	imes 0.001$).
- **Exit Fee:** Deducted upon closure ($	ext{exit\_price} 	imes 	ext{qty} 	imes 0.001$).
- **Same-Candle SL/TP:** Conservative resolution: if both SL and TP price levels are within high/low of the bar, SL is executed first (SL_HIT).

---

## 4. QUANTUM & GEMINI AI SCIENTIFIC AUDIT

- **Quantum Subsystem (quantum/):**
  - Evaluated on CPU simulation (PennyLane/Qiskit).
  - Physical QPU execution: 0.0 seconds.
  - Scientific Verdict: **B — NO QUANTUM ADVANTAGE DETECTED**. Zero execution authority.
- **Gemini Subsystem (gemini_service.py):**
  - Market insight caching and error degradation verified.
  - Scientific Verdict: Strictly advisory. Zero execution authority.

---

## 5. FINAL SCIENTIFIC RECOMMENDATION

1. Maintain **LIVE_TRADING_ENABLED = False** permanently.
2. Rely on **ProfitabilityGate** as a capital defense barrier against high-frequency taker fee decay.
3. Reject all claims of speculative alpha derived from in-sample artifact weights.
