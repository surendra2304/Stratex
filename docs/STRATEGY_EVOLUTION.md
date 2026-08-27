# Autonomous Strategy Evolution Laboratory Guide

## Overview

The Strategy Evolution Laboratory autonomously breeds, mutates, and tests quantitative strategy genomes, subjecting candidates to a 6-gate statistical gauntlet before paper incubation and production graduation.

---

## 1. Evolution Pipeline Architecture

```
┌────────────────────────────────────────────────────────────────────────┐
│                   Strategy Genetic Engine (50-100 Genomes)             │
│                      (evolution/genetic_engine.py)                     │
├────────────────────────────────────────────────────────────────────────┤
│ • Parametric Genome: Archetype, RSI, EMA, ADX, Bollinger, SL/TP        │
│ • Operators: Gaussian Mutation (±10-30%), Crossover, Tournament Select │
└───────────────────────────────────┬────────────────────────────────────┘
                                    │
                                    ▼
┌────────────────────────────────────────────────────────────────────────┐
│                   6-Gate Quantitative Validation Gauntlet              │
│                    (evolution/validation_gauntlet.py)                  │
├────────────────────────────────────────────────────────────────────────┤
│ • GATE 1: Backtest Profitability (PF >= 1.30, Trades >= 50)            │
│ • GATE 2: Walk-Forward Efficiency (WFE >= 0.50)                        │
│ • GATE 3: Monte Carlo Survival (95th %ile Drawdown <= 15.0%)           │
│ • GATE 4: Parameter Sensitivity (Degradation <= 30% under perturbation)│
│ • GATE 5: Regime Robustness (Profitable in >= 60% of Regimes)          │
│ • GATE 6: Overfitting Checks (Deflated Sharpe > 0, PBO <= 30%)         │
└───────────────────────────────────┬────────────────────────────────────┘
                                    │ (All 6 Gates Passed)
                                    ▼
┌────────────────────────────────────────────────────────────────────────┐
│                   Paper Trading Incubator (30+ Days)                   │
│                        (evolution/incubator.py)                        │
├────────────────────────────────────────────────────────────────────────┤
│ • Live vs Theoretical Signal Tracking                                  │
│ • Fidelity Correlation Metric >= 0.70                                  │
│ • Graduation Criteria: 30d, PF >= 1.25, Max Live DD <= 10%             │
└────────────────────────────────────────────────────────────────────────┘
```

---

## 2. Validation Gauntlet Thresholds

| Gate | Criterion | Threshold | Failure Action |
| :--- | :--- | :--- | :--- |
| **Gate 1** | Profit Factor & Trades | $PF \ge 1.30$, $N \ge 50$ | Discard genome, feedback to genetic engine |
| **Gate 2** | Walk-Forward Efficiency | $WFE \ge 0.50$ | Parameter overfitting penalty |
| **Gate 3** | Monte Carlo Drawdown | $DD_{95\%} \le 15.0\%$ | Reject high-variance genome |
| **Gate 4** | Parameter Sensitivity | $\Delta_{\text{perf}} \le 30.0\%$ | Flag parameter fragility / curve fitting |
| **Gate 5** | Regime Robustness | Profitable in $\ge 60\%$ regimes | Flag regime-dependent vulnerability |
| **Gate 6** | Overfitting (PBO / DSR) | $PBO \le 30.0\%$, $DSR > 0$ | Flag selection bias |

---

## 3. Paper Incubator & Graduation

1. **Admission**: Certified genomes are admitted to `incubator_state.json`.
2. **Forward Paper Execution**: The strategy paper-trades live market feeds for $\ge 30$ calendar days.
3. **Fidelity Tracking**: Tracks live execution price realization against backtest assumptions.
4. **Graduation**: If after 30 days the live Profit Factor $\ge 1.25$ and live Drawdown $\le 10\%$, the strategy is promoted to the production strategy candidate pool.
