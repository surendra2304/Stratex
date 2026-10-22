# STRATEX — Freqtrade-Inspired Quantitative Architecture Upgrade

## 1. Overview & Objectives

This document details the quantitative architecture upgrade integrated into **STRATEX**, adopting production-proven quantitative concepts from [Freqtrade](https://github.com/freqtrade/freqtrade) into Stratex's native Python architecture.

The objective is to enable Stratex to make **better researched, more selective, and risk-adjusted trades** through:
- Native Strategy Parameter Abstraction (`stratex_freqtrade_adapter.parameters`)
- Profitability-Aware Hyperparameter Optimization powered by Optuna (`stratex_freqtrade_adapter.optimizer`)
- Strict Out-of-Sample Walk-Forward Validation (`stratex_freqtrade_adapter.walkforward`)
- Conservative Pre-Trade Protections (`stratex_freqtrade_adapter.protections`)
- Direct Integration with Stratex's authoritative `BacktestEngine` (`stratex_freqtrade_adapter.stratex_bridge`)
- Non-Destructive Parameter Persistence (`optimization_results/`)
- Unified Dashboard Research Telemetry (`/api/optimization`)

---

## 2. Core Safety & Architectural Invariants

| Guardrail | Status | Enforcement Mechanism |
|---|---|---|
| **Permanent LIVE Trading Block** | ENFORCED | `execution.py` strictly blocks live order creation; cannot be overridden |
| **TESTNET Order Placement** | GATED | Only allowed through Stratex `ExecutionPolicy` and exchange authorization |
| **PAPER Mode Protection** | ENFORCED | Simulation only; zero external exchange orders |
| **Research Mode Isolation** | ENFORCED | `RESEARCH_MODE=1` env variable immediately blocks all order execution |
| **Zero Silent Overwrites** | ENFORCED | Optimizer never mutates `config_strategy.py`; promotions require human review |
| **Zero Credential Exposure** | ENFORCED | Credentials remain strictly environment-based; zero keys in results/logs |

---

## 3. Component Architecture

```
                               ┌────────────────────────────────┐
                               │  Historical Market Data Cache  │
                               │  (Binance 1h/15m OHLCV Candles)│
                               └───────────────┬────────────────┘
                                               │
                                               ▼
                               ┌────────────────────────────────┐
                               │    Walk-Forward Validator      │
                               │ (Rolling Train / Test Windows) │
                               └───────────────┬────────────────┘
                                               │
                        ┌──────────────────────┴──────────────────────┐
                        ▼                                             ▼
         ┌─────────────────────────────┐               ┌─────────────────────────────┐
         │      In-Sample Train        │               │   Out-of-Sample Test        │
         │ (Optuna Hyperoptimization)  │               │ (Untouched Validation Data) │
         └──────────────┬──────────────┘               └──────────────┬──────────────┘
                        │                                             │
                        ▼                                             ▼
         ┌─────────────────────────────┐               ┌─────────────────────────────┐
         │    StratexStrategyBridge    │               │    StratexStrategyBridge    │
         │  (BacktestEngine Simulation)│               │  (BacktestEngine Simulation)│
         └──────────────┬──────────────┘               └──────────────┬──────────────┘
                        │                                             │
                        └──────────────────────┬──────────────────────┘
                                               ▼
                               ┌────────────────────────────────┐
                               │ Auditable Optimization Results │
                               │  (optimization_results/*.json) │
                               └───────────────┬────────────────┘
                                               │
                                               ▼
                               ┌────────────────────────────────┐
                               │   Dashboard Research Engine    │
                               │    (/api/optimization UI)      │
                               └────────────────────────────────┘
```

---

## 4. Subsystem Details

### A. Parameter Abstraction (`parameters.py`)
Provides typed, validated hyperparameter containers:
- `IntParameter(low, high, default, step=1)`
- `RealParameter(low, high, default, step=None)`
- `CategoricalParameter(choices, default=None)`

Each container encapsulates boundary validation (`low <= high`, non-empty choices, valid defaults) and provides an Optuna suggestion helper `param.suggest(trial, name)`.

### B. Pre-Trade Protection Layer (`protections.py`)
Evaluated in the live execution pipeline between Profitability Gate and RiskGate:
$$\text{Signal} \to \text{Profitability Gate} \to \mathbf{\text{Protection Layer}} \to \text{RiskGate} \to \text{Execution}$$

1. **Stoploss Cooldown**: Blocks new entry signals on a symbol for 30 minutes following a `SL_HIT` exit.
2. **Stoploss Guard**: Blocks a symbol if $\ge 3$ stop-loss losses occur within the last 6 trades.
3. **Low Profit Pair Guard**: Temporarily halts symbols producing cumulative negative PnL over the last 8+ trades.
4. **Drawdown Guard**: Blocks all new entries when portfolio drawdown from peak reaches or exceeds 5%.

### C. Profitability-Aware Objective (`optimizer.py`)
The Optuna objective function strictly evaluates net trading expectancy under realistic friction:
$$\text{Score} = \text{Net PnL} + 100 \times \text{Sharpe} + 50 \times \text{Sortino} + 1000 \times \max(0, \text{PF} - 1.0) - \text{Penalties}$$
- **Sample Size Penalty**: If trades $< \text{min\_trades}$, heavily penalizes score ($-1{,}000{,}000$).
- **Drawdown Penalty**: Penalizes excess drawdown beyond `max_drawdown_pct` ($20{,}000 \times \Delta$).
- **Weak PF Penalty**: Penalizes profit factors below `target_profit_factor` ($10{,}000 \times \Delta$).

### D. Chronological Walk-Forward Validation (`walkforward.py`)
Generates strictly rolling, non-overlapping train and test splits to detect and prevent curve-fitting and data leakage. Parameters optimized solely on the in-sample window must demonstrate stability and positive net return on the out-of-sample window.

---

## 5. Parameter Persistence & Dashboard Telemetry

Optimization runs are exported to `optimization_results/` with complete audit metadata:
- Git commit SHA
- Exact date/time and data range
- Timeframe and symbols evaluated
- Friction parameters (taker fee = 0.001, slippage = 0.0005)
- Best parameter values
- In-sample vs out-of-sample performance breakdown
- Promotion status (`RESEARCH ONLY`, `OOS VALIDATED`, `APPROVED`, `ACTIVE`)

The Stratex dashboard displays these metrics live at `/api/optimization` with a dedicated terminal UI panel.
