# Quantitative Algorithmic Trading & Forward Validation Framework

A robust, multi-strategy quantitative trading and statistical validation platform built in Python. Designed with strict risk controls, zero-credential-leak architecture, reproducible backtesting, and two distinct execution tracks (**Paper Engine** and **Binance Testnet Engine**).

![Python](https://img.shields.io/badge/Python-3.11-blue?logo=python)
![Exchange](https://img.shields.io/badge/Exchange-Binance%20Testnet-yellow?logo=binance)
![Tests](https://img.shields.io/badge/Tests-289%20Passing-brightgreen)
![Status](https://img.shields.io/badge/Live%20Trading-BLOCKED-red)
![License](https://img.shields.io/badge/License-MIT-green)

---

## Current Status & Deployment Gates

> [!CAUTION]
> **Live real-money trading is strictly BLOCKED.** Passing 289 software tests proves system correctness and risk-gate integrity only. It does **not** prove an economic edge. Live trading is gated until genuine out-of-sample forward validation criteria are satisfied.

### Deployment Gate Checklist (from `backtest_results/phase15/FINAL_REPORT.md`)

| Criterion / Deployment Gate | Required Status | Current State | Notes |
|---|---|---|---|
| **Zero Credential Exposure** | PASS |  PASS | Environment-variable isolation; git history sanitized |
| **Zero Live Orders Placed** | PASS |  PASS | `ExecutionPolicy` strictly blocks live order creation |
| **State Corruption Invariants** | PASS |  PASS | Atomic file writes (`.tmp` -> rename) with corruption fallback |
| **Kill Switch Cost Attribution** | PASS |  PASS | `CostEngine` slippage & taker fees applied on forced exits |
| **Monte Carlo Benchmark Parity** | PASS |  PASS | Identical cost model used across strategy and benchmarks |
| **Statistical Significance ($p < 0.05$)** | REQUIRED | ❌ UNPROVEN | Requires $\ge 30$ genuine forward trades |
| **Positive Net Expectancy After Costs**| REQUIRED | ❌ UNPROVEN | Microsecond friction & fees must be overcome out-of-sample |
| **Profit Factor $\ge 1.20$** | REQUIRED | ❌ UNPROVEN | Pre-registered quantitative acceptance threshold |
| **Genuine Forward Validation Duration**| $\ge 30$ Days |  RUNNING | Actively running in `paper_engine` (`forward_exp_001`) |

---

## Architecture Overview: Two Execution Tracks

The repository maintains two independent execution tracks governed by the `TRADING_MODE` configuration (`PAPER` vs `TESTNET`):

```
                       ┌──────────────────────────────────────┐
                       │           Market Data Feeds          │
                       │    (Binance REST & WebSocket API)    │
                       └──────────────────┬───────────────────┘
                                          │
                                          ▼
                       ┌──────────────────────────────────────┐
                       │      Signal Generation & Funnel      │
                       │ (ADX+EMA, Supertrend, Swing, ML, ...)│
                       └──────────┬────────────────┬──────────┘
                                  │                │
             ┌────────────────────┘                └────────────────────┐
             ▼                                                          ▼
┌───────────────────────────────┐                          ┌───────────────────────────────┐
│     TRACK 1: PAPER ENGINE     │                          │   TRACK 2: TESTNET ENGINE     │
│   (Zero External Orders)      │                          │ (Real Binance Spot Testnet)   │
├───────────────────────────────┤                          ├───────────────────────────────┤
│ • Pure simulated execution    │                          │ • Real Testnet order placement│
│ • Realistic CostEngine model  │                          │ • Dynamic symbol discovery    │
│ • Frozen immutable experiment │                          │ • Multi-asset scanner         │
│ • Automated reconciliation    │                          │ • Multi-tier risk & profit gate│
│ • Process & market heartbeats │                          │ • Live position monitor & SL/TP│
│ • Statistical classification  │                          │ • Real exchange reconciliation│
└──────────────┬────────────────┘                          └──────────────┬────────────────┘
               │                                                          │
               └────────────────────┬─────────────────────────────────────┘
                                    ▼
                       ┌──────────────────────────────┐
                       │     Unified Dashboard UI     │
                       │   (Routes by TRADING_MODE)   │
                       └──────────────────────────────┘
```

1. **Paper Validation Track (`paper_engine/`)**:
   - Zero-order paper execution simulator designed for statistical validation.
   - Integrates `FrozenExperimentConfig` (persisted immutable configs with Git SHA tracking), `PaperReconciliation` for duplicate ledger prevention, `HeartbeatState` and `DataMonitor` for feed freshness, and `KillSwitch`.
   - Entry point: `paper_forward_runner.py`.

2. **Testnet Execution Track (`testnet_engine/`)**:
   - Executes real spot testnet orders via `python-binance`.
   - Features `SymbolDiscovery` for market universe filtering, `MarketScanner` for live signal generation, `RiskGate` for portfolio exposure / daily loss enforcement, `ProfitabilityGate` with taker fee / spread coverage, and `PositionProtection` for real-time stop-loss and take-profit lifecycle tracking.
   - Entry point: `bot.py`.

3. **Web Dashboard (`dashboard.py`)**:
   - Modern dark-mode monitoring terminal with real-time WebSocket clock, portfolio telemetry, and signal funnel statistics.
   - Automatically adapts routes and metrics based on `TRADING_MODE` (`PAPER` or `TESTNET`).

---

## Trading Strategies

The platform supports six modular quantitative strategies:

| Strategy | File | Core Mechanism | Timeframe |
|---|---|---|---|
| **ADX + EMA Trend** | [`strategy_adx_ema.py`](strategy_adx_ema.py) | 200 EMA trend filter with 9/21 EMA momentum and ADX > 25 confirmation | 5m / 15m / 1H |
| **Supertrend** | [`strategy_supertrend.py`](strategy_supertrend.py) | Volatility-adjusted ATR band trend following | 5m / 15m |
| **Swing Trader** | [`strategy_swing.py`](strategy_swing.py) | MACD signal line crossover filtered by 200 EMA baseline | 1H / 4H |
| **ML Predictor** | [`strategy_ml.py`](strategy_ml.py) | Random Forest & XGBoost classifiers trained on backward-looking features | 5m / 15m |
| **Scalper** | [`strategy_scalper.py`](strategy_scalper.py) | Mean-reversion via RSI oversold/overbought and Bollinger Bands | 1m / 5m |
| **The Aggressor** | [`strategy_aggressor.py`](strategy_aggressor.py) | Order book volume delta and bid-ask imbalance scalper | Tick / 1m |

---

## Project Structure

```
.
├── account_client.py           # Authenticated Binance account query wrapper
├── backtester.py               # Classical historical backtest runner
├── backtest_engine.py          # Vectorized and event-driven backtest framework
├── bot.py                      # Main entry point for the Testnet trading bot
├── config.py                   # Central configuration (loads environment variables)
├── config_strategy.py          # Strategy parameter definitions
├── config_template.py          # Reference template for .env configuration
├── dashboard.py                # Flask + WebSocket dashboard UI (PAPER & TESTNET)
├── data.py                     # Historical and live data loading utilities
├── data_client.py              # Binance market data REST client
├── diagnostics.py              # System health and environment diagnostics
├── execution.py                # Low-level Binance order routing and safety gates
├── features.py                 # Backward-looking technical feature engineering
├── logger.py                   # Structured JSON and CSV logging
├── metrics.py                  # PnL, Sharpe, Sortino, Drawdown calculation engine
├── ml_research.py              # Machine learning training and feature evaluation
├── paper_forward_runner.py     # Long-running paper forward validation daemon
├── regime.py                   # Market volatility and regime detection
├── status_check.py             # CLI status report for account and market data
│
├── paper_engine/               # Track 1: Paper trading & statistical validation
│   ├── alerts.py               # Internal alerting mechanism
│   ├── benchmark.py            # Monte Carlo and random trade benchmarks
│   ├── config.py               # Paper engine capital and sizing defaults
│   ├── data_monitor.py         # Market data gap and staleness monitor
│   ├── experiment_config.py    # Frozen immutable experiment configurations
│   ├── funding_simulator.py    # Funding rate and carry trade simulation
│   ├── heartbeat.py            # Process and subsystem heartbeat tracking
│   ├── kill_switch.py          # Safe liquidation trigger with realistic costs
│   ├── portfolio.py            # Paper portfolio state, cash, margin, and equity
│   ├── reconciliation.py       # Paper ledger uniqueness and reconciliation engine
│   ├── session.py              # Paper execution session management
│   ├── signal_logger.py        # Durable forward signal logger
│   └── statistical_report.py   # Statistical significance and classification gates
│
├── testnet_engine/             # Track 2: Binance Spot Testnet execution
│   ├── discovery.py            # Dynamic market symbol universe discovery
│   ├── market_scanner.py       # Multi-asset real-time signal scanner
│   ├── profitability_gate.py   # Fee and spread hurdle validation
│   ├── protection.py           # Real-time SL/TP and position protection
│   ├── report_quality.py       # Execution telemetry and fill quality tracking
│   ├── risk_gate.py            # Daily loss, drawdown, and position limits
│   └── service.py              # Testnet orchestrator service
│
├── backtest_results/           # Research history & audit reports (Phases 4–15)
│   └── phase15/                # Phase 15 Final Quantitative Audit Report
├── experiments/                # Immutable frozen experiment JSON registry
├── research_phase6/ – phase10/ # Phase-specific historical research notebooks & scripts
├── static/                     # Dashboard frontend assets (JS, CSS, HTML templates)
└── tests/                      # Pytest comprehensive test suite (289 tests)
```

---

## Getting Started

### 1. Prerequisites
- Python 3.11+
- Binance Spot Testnet API Key & Secret ([Binance Testnet Portal](https://testnet.binance.vision))

### 2. Installation
```bash
# Clone repository
git clone https://github.com/surendra2304/algorithmic-trading-bot.git
cd algorithmic-trading-bot

# Install dependencies
pip install -r requirements.txt
```

### 3. Environment Configuration
Create a `.env` file in the root directory (or copy from `.env.example`):

```bash
# --- Exchange Credentials ---
API_KEY="your_binance_testnet_api_key"
SECRET_KEY="your_binance_testnet_api_secret"

# --- Execution Track Mode ---
# Options: "PAPER" (internal simulation) or "TESTNET" (Binance Testnet orders)
TRADING_MODE="TESTNET"

# --- Safety Controls ---
TESTNET_ENABLED="True"          # Must be True to place Testnet orders
LIVE_TRADING_ENABLED="False"    # Hardcoded safety: NEVER set to True
PAPER_SAFE_MODE="True"
RESEARCH_MODE="0"

# --- Optional Isolated State Paths ---
# FORWARD_RECONCILIATION_FILE="forward_reconciliation.jsonl"
```

---

## Running the Bot & Services

### Running Track 2: Binance Testnet Bot
```bash
# Run the live testnet scanner and execution engine
python bot.py
```

### Running Track 1: Paper Forward Validation
```bash
# Run the long-running paper validation runner with frozen experiment tracking
python paper_forward_runner.py
```

### Running the Web Dashboard
```bash
# Start the monitoring web server (defaults to http://127.0.0.1:5000)
python dashboard.py
```

### Running the Test Suite
```bash
# Run all 289 automated unit, integration, and accounting fuzz tests
pytest
```

---

## Security & Risk Notice

- **No Real Capital at Risk**: Default configuration targets Binance Testnet and internal paper simulations with zero financial liability.
- **Credential Protection**: Do not commit `.env` or any files containing private keys.
- **Disclaimer**: This codebase is for quantitative research and software development purposes. Quantitative models are subject to regime shifts, liquidity constraints, and execution slippage.

---

*Maintained by [Surendra](https://github.com/surendra2304)*
