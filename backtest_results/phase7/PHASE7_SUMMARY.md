# PHASE 7 EXECUTIVE SUMMARY

### 1. Did CVD add predictive value?
CVD features improved model entropy slightly over raw technicals, but PR-AUC remains borderline due to intense noise on 1m timeframe.

### 2. Did volume features add value?
Volume shocks effectively identify breakout initiation, but mean-reversion signals frequently suffer from volume-induced momentum traps.

### 3. Did fractional differencing add value?
Fractional differencing (d=0.3) successfully achieved stationarity without losing all memory, offering better generalized feature inputs for XGBoost.

### 4. Did triple-barrier labeling improve the ML target?
Yes. By strictly separating Timeouts from Stop Losses, the model no longer attempts to learn pure noise states.

### 5. Final Classification of Features
- **CVD (Normalized)**: B - Promising
- **Fractional Differencing**: B - Promising
- **Naive EMA/RSI**: D - Reject
- **Wick Asymmetry**: C - Inconclusive
