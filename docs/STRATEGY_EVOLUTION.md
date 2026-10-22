# Autonomous Strategy Evolution & Human Governance Guide

## Overview

The Strategy Evolution Laboratory autonomously discovers, breeds, mutates, and tests quantitative strategy genomes, subjecting candidates to a 6-gate statistical gauntlet and a 30-day paper incubator before human operator review.

---

## 🏛️ Strategy Evolution Pipeline

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
│ • Fidelity Correlation Metric >= 0.60, Live PF >= 1.10                 │
└───────────────────────────────────┬────────────────────────────────────┘
                                    │ (Incubation Criteria Met)
                                    ▼
┌────────────────────────────────────────────────────────────────────────┐
│                   Human Approval Gate (Mandatory Review)               │
│                     (evolution/approval_gates.py)                      │
├────────────────────────────────────────────────────────────────────────┤
│ ⚠️ INVARIANT: NO strategy reaches live without human approval.         │
│ • Cryptographic audit hash generated for evidence package.             │
│ • Operator reviews evidence & signs approval via Dashboard / API.      │
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

## 3. Paper Incubator & Human Approval Lifecycle

1. **Admission**: Certified genomes enter `incubator_state.json`.
2. **Forward Paper Execution**: The strategy forward-trades live market feeds for $\ge 30$ calendar days.
3. **Fidelity Tracking**: Tracks live execution price realization against backtest assumptions ($Fidelity \ge 0.60, PF \ge 1.10$).
4. **Promotion Proposal**: Submits formal proposal with cryptographic audit hash into `approval_queue.json`.
5. **Human Approval**: The operator reviews the evidence package and approves via `POST /api/evolution/approve/{id}`.

---

## 4. Evolution Dashboard & Governance APIs

- `GET /api/evolution/status` — Population size, active generation, incubating strategy count, pending approvals.
- `GET /api/evolution/approvals` — Pending and historical proposal queue.
- `POST /api/evolution/approve/<proposal_id>` — Signs and records human approval.
