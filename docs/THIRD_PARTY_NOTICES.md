# Third-Party Notices & Attribution

STRATEX adapts selective architectural patterns, interface contracts, and concepts from leading open-source quantitative trading and algorithmic research projects.

No upstream repositories are vendored or copied wholesale. All adapters, models, and execution layers are clean-room, Stratex-native implementations maintaining Stratex's authoritative safety controls (`ExecutionPolicy`, `RiskGate`, `ProfitabilityGate`, `PositionProtection`).

---

## Studied Upstream Projects & Ported Concepts

### 1. VectorBT
- **Upstream Repository**: [https://github.com/polakowo/vectorbt](https://github.com/polakowo/vectorbt)
- **License**: Apache-2.0 with Commons Clause
- **Ported Architectural Concepts**:
  - High-throughput parameter sweep generator (`SweepSpec`).
  - Standardized vectorized candidate evaluation metrics (`net_pnl`, `profit_factor`, `max_drawdown`, `sharpe`).
  - Strict research-accelerator boundary: all candidates require canonical `BacktestEngine` confirmation before deployment.
- **Location in Stratex**: `stratex_vectorbt/`

### 2. NautilusTrader
- **Upstream Repository**: [https://github.com/nautechsystems/nautilus_trader](https://github.com/nautechsystems/nautilus_trader)
- **License**: LGPL-3.0
- **Ported Architectural Concepts**:
  - Deterministic event bus (`DeterministicRuntime`).
  - Monotonic nanosecond timestamp verification rejecting time-travel.
  - Typed event stream (`MarketEvent`, `OrderIntent`, `RuntimeState`).
  - Event replay determinism and fault tracking.
- **Location in Stratex**: `stratex_nautilus/`

### 3. Jesse
- **Upstream Repository**: [https://github.com/jesse-ai/jesse](https://github.com/jesse-ai/jesse)
- **License**: MIT
- **Ported Architectural Concepts**:
  - Typed strategy hyperparameter schema (`HyperParameter`).
  - Chronological train/test optimization splits (`OptimizationSplit`).
  - Out-of-sample degradation calculation (`degradation()`) to detect and reject severe overfitting.
- **Location in Stratex**: `stratex_jesse/`

### 4. Hummingbot
- **Upstream Repository**: [https://github.com/hummingbot/hummingbot](https://github.com/hummingbot/hummingbot)
- **License**: Apache-2.0
- **Ported Architectural Concepts**:
  - Reusable order-book snapshots (`OrderBookSnapshot`).
  - Top-$N$ order book depth imbalance calculation (`OrderBookImbalance`).
  - Spread, mid-price, and stale order-book entry rejection.
  - Connector health abstractions (`ConnectorHealth`).
- **Location in Stratex**: `stratex_hummingbot/`

### 5. QuantConnect LEAN
- **Upstream Repository**: [https://github.com/QuantConnect/Lean](https://github.com/QuantConnect/Lean)
- **License**: Apache-2.0
- **Ported Architectural Concepts**:
  - Modular strategy architecture: `Alpha/Signal` $\to$ `Portfolio Construction` $\to$ `Risk Management` $\to$ `Execution` (`AlphaRiskExecutionPipeline`).
  - Standardized `Insight` and `PortfolioTarget` dataclasses.
- **Location in Stratex**: `stratex_lean/`

### 6. QuantDinger
- **Upstream Repository**: [https://github.com/OpenByteInc/QuantDinger](https://github.com/OpenByteInc/QuantDinger)
- **License**: Apache-2.0
- **Ported Architectural Concepts**:
  - Immutable strategy registry with SHA-256 source code hashing and frozen parameter snapshots (`StrategyRegistry`).
  - Explicit lifecycle state machine (`RESEARCH` $\to$ `OOS_VALIDATED` $\to$ `APPROVED` $\to$ `ACTIVE` $\to$ `RETIRED`).
  - Durable finite research jobs (`JobStore`, `ResearchJobRunner`).
  - Time-bounded worker leases and heartbeat supervision (`RuntimeLease`, `RuntimeSupervisor`).
  - Idempotent execution intents (`ExecutionIntent`, `IdempotencyGuard`).
  - Isolated AI agent research boundary (`ResearchAgentGateway`).
- **Location in Stratex**: `stratex_quantdinger/`

### 7. Freqtrade
- **Upstream Repository**: [https://github.com/freqtrade/freqtrade](https://github.com/freqtrade/freqtrade)
- **License**: GPL-3.0
- **Ported Architectural Concepts**:
  - Multi-factor Optuna objective optimization.
  - Rolling walk-forward validation windows.
  - Pre-trade protections (Stoploss Cooldown, Stoploss Guard, Drawdown Guard, Low Profit Pair Guard).
- **Location in Stratex**: `stratex_freqtrade_adapter/`

### 8. CCXT
- **Upstream Repository**: [https://github.com/ccxt/ccxt](https://github.com/ccxt/ccxt)
- **License**: MIT
- **Ported Architectural Concepts**:
  - Unified multi-exchange data and execution abstraction layer.
  - Bidirectional symbol normalization, precision flooring, and error categorization.
- **Location in Stratex**: `stratex_ccxt_adapter/`
