# Stratex + QuantDinger Architectural Integration

## Overview

This subsystem implements architectural patterns adapted from [QuantDinger](https://github.com/OpenByteInc/QuantDinger.git) into **STRATEX**. The integration elevates Stratex's infrastructure into an institutional-grade, auditable, and restart-safe algorithmic trading framework.

---

## 1. Architectural Topology

```
                                 STRATEX PLATFORM
                                        │
                             ┌──────────▼──────────┐
                             │  StrategyRegistry   │ (Immutable versions, SHA-256 code hash,
                             └──────────┬──────────┘  frozen parameters, lifecycle states)
                                        │
                                Versioned Strategy
                                        │
                             ┌──────────▼──────────┐
                             │  Research Pipeline  │ (JobStore + ResearchJobRunner)
                             │  (Optuna / FreqT)   │
                             └──────────┬──────────┘
                                        │
                          Backtest → Optimize → Walk-Forward
                                        │
                                  OOS Evidence
                                        │
                             ┌──────────▼──────────┐
                             │    Human Approval   │ (Explicit promotion: APPROVED -> ACTIVE)
                             └──────────┬──────────┘
                                        │
                             ┌──────────▼──────────┐
                             │   Trading Runtime   │ (RuntimeLease + RuntimeSupervisor
                             └──────────┬──────────┘  heartbeats, lease expiration gates)
                                        │
                                     Signal
                                        │
                             ┌──────────▼──────────┐
                             │   ExecutionIntent   │ (Explicit typed contract +
                             └──────────┬──────────┘  IdempotencyGuard deduplication)
                                        │
                             ┌──────────▼──────────┐
                             │ Pre-Trade Protection│ (Stoploss Cooldown, Loss Guards)
                             └──────────┬──────────┘
                                        │
                             ┌──────────▼──────────┐
                             │  ProfitabilityGate  │ (Microstructural edge > costs)
                             └──────────┬──────────┘
                                        │
                             ┌──────────▼──────────┐
                             │      RiskGate       │ (Portfolio risk, max DD, sizing)
                             └──────────┬──────────┘
                                        │
                             ┌──────────▼──────────┐
                             │ PositionProtection  │ (Bracket SL/TP orders)
                             └──────────┬──────────┘
                                        │
                             ┌──────────▼──────────┐
                             │   ExecutionPolicy   │ (Paper block / Live forbidden)
                             └──────────┬──────────┘
                                        │
                             ┌──────────▼──────────┐
                             │ CCXT / Exchange     │ (Unified order normalization)
                             └──────────┬──────────┘
                                        │
                                 Testnet Broker
```

---

## 2. Core Subsystems

### A. Immutable Strategy Registry (`stratex_quantdinger/registry.py`)
- **Immutability Guarantee**: Every strategy version is bound to an exact SHA-256 source hash and frozen parameter dictionary. If code or parameters change for a given semantic version, a `ValueError` is raised.
- **Lifecycle State Machine**:
  $$\text{RESEARCH} \longrightarrow \text{OOS\_VALIDATED} \longrightarrow \text{APPROVED} \longrightarrow \text{ACTIVE} \longrightarrow \text{RETIRED}$$
- **Single Active Invariant**: Promoting a new version to `ACTIVE` automatically retires any currently active version for that strategy.
- **Audit Logging**: Every transition is appended with correlation IDs to `quantdinger_audit.jsonl`.

### B. Durable Research Jobs & Worker Decoupling (`stratex_quantdinger/jobs.py`)
- **Decoupled Architecture**: HTTP/API threads never execute long-running backtests, Optuna hyperparameter searches, or walk-forward validation.
- **`JobStore`**: Durable atomic persistence (`experiment_jobs.json`) tracking `job_id`, `job_type`, `status` (`QUEUED`, `RUNNING`, `COMPLETED`, `FAILED`, `CANCELLED`), `progress` ($0.0 \to 1.0$), `result`, and `error`.
- **`ResearchJobRunner`**: Background daemon execution thread pool running research tasks and reporting progress.

### C. Runtime Leases & Health Supervision (`stratex_quantdinger/runtime.py`)
- **`RuntimeLease`**: Time-bounded operational lease (default: 60 seconds) renewed by active workers.
- **`RuntimeHeartbeat`**: Telemetry heartbeat emitted periodically (every 20s).
- **`RuntimeSupervisor`**: Evaluates lease validity and health. If a lease expires:
  - New execution intents are strictly blocked (`UNHEALTHY_RUNTIME_LEASE_LEASE_EXPIRED`).
  - Existing open position protections (OCO brackets) remain active.
  - No blind liquidation occurs due to transient network latency.

### D. Idempotent Execution Intents (`stratex_quantdinger/idempotency.py`)
- **`ExecutionIntent`**: Explicit typed contract specifying `intent_id`, `strategy_id`, `strategy_version`, `symbol`, `side`, `quantity`, `order_type`, `price`, and `paper_only`.
- **`IdempotencyGuard`**: Prevents duplicate order placement across worker retries, server restarts, and ambiguous exchange network timeouts.
- **Order Correlation**: Intent records reference exchange order IDs upon settlement.

### E. AI Agent Boundary (`stratex_quantdinger/agent_contract.py`)
- **`ResearchAgentGateway`**: Dedicated agent-safe interface.
- External agents and scripts may submit backtests, optimizations, and walk-forward jobs, inspect job progress, and read registered strategy versions.
- **Security Invariant**: The gateway contains zero order-placement, trading, or private key execution methods.

---

## 3. Operational Endpoints

| Endpoint | Method | Purpose |
|---|---|---|
| `/api/strategy-registry` | `GET`, `POST` | Lists registered versions or registers an immutable version |
| `/api/strategy-registry/promote` | `POST` | Explicit human/operator promotion across lifecycle states |
| `/api/research-jobs` | `GET`, `POST` | Lists durable jobs or submits an asynchronous research task |
| `/api/research-jobs/<job_id>` | `GET` | Fetches progress, execution status, and result metrics |
| `/api/runtime-status` | `GET` | Telemetry on active runtime leases, heartbeats, and supervisor health |
| `/api/execution-intents` | `GET` | Lists recent execution intents and mapped exchange order IDs |
| `/api/agent-gateway/jobs` | `GET`, `POST` | Agent-safe research submission and inspection interface |

---

## 4. Verification & Regression Metrics

- **Unit & Integration Tests**: 13/13 QuantDinger tests passed.
- **Combined Subsystems**: 55/55 integration tests passed (`QuantDinger` + `Freqtrade` + `CCXT` + `Frontend Contracts` + `Testnet Engine`).
- **Full Suite**: 732/732 tests passed (100% green, 0 failures, 0 regressions).
