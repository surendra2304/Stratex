# Stratex Call-Path Matrix: Strategy, Risk, OMS & Advisory Lifecycle

**Prepared:** 21 October 2026  
**Status:** Canonical architectural call-path audit for Stratex inside the FRIDAY Universe  
**Authority Invariant:** All live-money paths are permanently disabled (`LIVE_FORBIDDEN_BY_DESIGN`). Execution is strictly restricted to Binance USDⓈ-M Futures Testnet / Demo Trading and IC Markets MT5 Demo.

---

## 1. Classification Legend

| Status | Meaning |
| :--- | :--- |
| **`LEGACY`** | Historical direct-API or file-based implementation retained for compatibility; planned for deprecation. |
| **`MIGRATED`** | Refactored into the unified, durable `TradeIntent` -> `StratexOrderManager` -> Venue Adapter pipeline. |
| **`TESTED`** | Verified via comprehensive unit and mocked integration tests in CI. |
| **`DEMO-TESTED`**| Tested against sandbox/testnet API endpoints with live demo-account credentials. |
| **`BLOCKED`** | Permanently prohibited by policy (e.g. live-capital execution) or disabled by configuration flags. |

---

## 2. Strategy Entry Call Paths

```
Strategy Signal Proposal
   │
   ├── [Legacy Flag = OFF] ──> execution._place_futures_market_order() ──> client.futures_create_order() [LEGACY]
   │
   └── [STRATEX_ORDER_MANAGER_ENABLED = true]
         │
         ├── TradeIntent(venue, environment, symbol, side, order_type, qty, sl, tp, client_order_id)
         ├── DeterministicRiskAgent.authorize(intent, risk_context)
         │      ├── ExecutionPolicy.can_place_order() [Zero Live Invariant]
         │      ├── Binance RiskGate / MT5 Risk Limits ($1,000 margin cap, 5x max leverage)
         │      └── PortfolioRiskGate (5% daily drawdown, fresh FX rates, broker equity)
         │
         ├── StratexOrderManager.submit(intent)
         │      ├── OrderIntentStore.reserve(intent) [Durable SQLite / PostgreSQL]
         │      ├── MemoraAudit.publish_trade_intent_audit() [Non-blocking]
         │      │
         │      ├── [venue == 'binance'] ──> BinanceFuturesOMSAdapter.submit_intent() [MIGRATED, TESTED]
         │      └── [venue == 'mt5']     ──> MT5BridgeAdapter.submit_intent()       [MIGRATED, TESTED]
```

### Detailed Entry Paths

| Call Path | Source File & Line | Target Function | Status | Notes |
| :--- | :--- | :--- | :--- | :--- |
| **Binance Market Entry (Legacy)** | `execution.py:835-969` | `_place_futures_market_order()` | `LEGACY`, `TESTED` | Active when `STRATEX_ORDER_MANAGER_ENABLED=false`. Uses `active_trades.json`. |
| **Binance Market Entry (OMS)** | `execution.py:714-770`, `stratex_agents/oms.py:70-135` | `_place_futures_market_order_via_oms()`, `StratexOrderManager.submit()` | `MIGRATED`, `TESTED` | Controlled via `STRATEX_ORDER_MANAGER_ENABLED=true`. Idempotent reservation. |
| **Binance Limit Entry (OMS)** | `stratex_agents/venue/binance_futures_oms_adapter.py:330-392` | `BinanceFuturesOMSAdapter.submit_intent()` | `MIGRATED`, `TESTED` | Pairs limit entry with working Algo SL/TP. Auto-cancels entry on protection error. |
| **MT5 Demo Market Entry** | `exchanges/mt5_bridge_adapter.py:270-320` | `MT5BridgeAdapter.submit_intent()` | `MIGRATED`, `TESTED` | Calls `/v1/orders/check` then `/v1/orders`. Enforces account currency and $1,000 margin cap. |
| **MT5 Pending Orders (Limit/Stop)** | `mt5_bridge/server.py:588-598` | `POST /v1/orders` | `MIGRATED`, `TESTED` | Validates broker contract sizes, tick sizes, and attaches protective stops. |
| **Spot Testnet Entry** | `execution.py:210-250` | `_place_market_order()` | `BLOCKED` | Spot testnet is non-priority; repository standardized on USDⓈ-M Futures & MT5. |
| **Live Capital Execution** | `execution.py:82-110` | `ExecutionPolicy.can_place_order()` | `BLOCKED` | **PERMANENTLY BLOCKED BY DESIGN**. Raises `RuntimeError` immediately if attempted. |

---

## 3. Protective Exit & Bracket Call Paths

| Call Path | Source File & Line | Target Function | Status | Invariants Enforced |
| :--- | :--- | :--- | :--- | :--- |
| **Binance Futures Algo Bracket** | `stratex_agents/venue/binance_futures_oms_adapter.py:210-260` | `_place_protection()` | `MIGRATED`, `TESTED` | Submits `STOP_MARKET` and `TAKE_PROFIT_MARKET` Algo Orders with `closePosition="true"`. |
| **Binance Fallback Safety Close** | `stratex_agents/venue/binance_futures_oms_adapter.py:225-245` | `_emergency_close_and_cancel_stop()` | `MIGRATED`, `TESTED` | If bracket fails, market-closes position immediately and cancels acknowledged stop. |
| **Legacy Futures Bracket** | `testnet_engine/protection.py:500-625` | `place_futures_bracket_protection()` | `LEGACY`, `TESTED` | Used by legacy `execution.py`. Direct API call to `futures_create_order`. |
| **MT5 Attached Protection** | `mt5_bridge/server.py:601-620` | `_mt5_entry_financials()` | `MIGRATED`, `TESTED` | Rejects any entry without both `stop_loss` and `take_profit` defined at order send time. |
| **Legacy Spot OCO Protection** | `testnet_engine/protection.py:100-240` | `place_oco_protection()` | `LEGACY`, `TESTED` | Spot Testnet dual-leg OCO order API. |

---

## 4. Trailing Stops & Dynamic Position Management

| Call Path | Source File & Line | Target Function | Status | Notes |
| :--- | :--- | :--- | :--- | :--- |
| **Binance Trailing Engine** | `testnet_engine/trailing.py:40-150` | `process_trailing_stops()` | `LEGACY`, `TESTED` | Inspects high-water mark; tightens stop-loss once breakeven threshold is passed. |
| **MT5 Ticket Modification** | `exchanges/mt5_bridge_adapter.py:230-260` | `modify_position_protection()` | `MIGRATED`, `TESTED` | Updates SL/TP by ticket. Never modifies positions globally by symbol. |
| **Bracket Status Checker** | `testnet_engine/protection.py:703-755` | `check_futures_bracket_status()` | `LEGACY`, `TESTED` | Detects when TP/SL has fired and marks position closed in trade ledger. |

---

## 5. Emergency Halts, Kill Switches & Close Call Paths

| Call Path | Source File & Line | Target Function | Status | Action Triggered |
| :--- | :--- | :--- | :--- | :--- |
| **Atomic Emergency Halt (API)** | `dashboard.py:6196-6260` | `POST /api/v1/emergency-halt` | `MIGRATED`, `TESTED` | Writes panic lock (`KILL_SWITCH_ACTIVE.lock`), cancel-all on Binance & MT5, sets OMS to `LOCKED`, audits to Memora. |
| **Manual Flatten Endpoint** | `dashboard.py:6150-6175` | `POST /api/live/emergency/flatten`| `MIGRATED`, `TESTED` | Engages order block (`panic=True, pause=True, kill_switch_lock=True`). |
| **Trading Pause Endpoint** | `dashboard.py:6176-6195` | `POST /api/live/emergency/halt` | `MIGRATED`, `TESTED` | Pauses new entries (`pause=True`); preserves existing protective exits. |
| **Futures Emergency Close** | `testnet_engine/protection.py:642-680` | `emergency_futures_market_close()` | `LEGACY`, `TESTED` | Issues market order with `reduceOnly=True` to close position immediately. |
| **MT5 Ticket Close** | `mt5_bridge/server.py:732-760` | `POST /v1/positions/<ticket>/close`| `MIGRATED`, `TESTED` | Closes position by exact ticket number using broker deal `TRADE_ACTION_DEAL`. |

---

## 6. Reconciliation & State Persistence Call Paths

| Call Path | Source File & Line | Target Function | Status | Notes |
| :--- | :--- | :--- | :--- | :--- |
| **Order State Reconciler** | `stratex_agents/reconciliation_agent.py:70-175` | `OrderReconciliationAgent.reconcile_once()` | `MIGRATED`, `TESTED` | Queries unresolved intents (`UNKNOWN`, `SUBMITTING`, `ACCEPTED`). Reconciles only on verifiable venue evidence. |
| **Equity Baseline Persistence** | `stratex_agents/reconciliation_agent.py:180-240`, `stratex_agents/portfolio_risk_state.py:80-160` | `reconcile_portfolio_equity()` | `MIGRATED`, `TESTED` | Persists day-start equity and high-water mark into `portfolio_equity_state` (Dokploy PostgreSQL / SQLite). |
| **Supervised Reconciliation Daemon** | `scripts/reconciliation_worker.py:40-110` | `reconciliation_loop()` | `MIGRATED`, `TESTED` | Supervised cycle executing intent reconciliation and multi-venue equity snapshotting. |
| **MT5 Bridge Order Lookup** | `mt5_bridge/server.py:660-690` | `GET /v1/orders/by-client-id/<id>` | `MIGRATED`, `TESTED` | Prevents re-submission on ambiguous timeout by exposing persisted bridge records. |

---

## 7. Cross-Agent Advisory Contracts (Phase E)

```
External Advisory Mesh (Advisory Only — Zero Execution Authority)
   │
   ├── Futuris (http://futuris:8000)
   │      └── FuturisContractClient.get_forecast()
   │             └── Direction, Horizon, Uncertainty, Calibration
   │
   ├── IntelX (http://intelx:8000)
   │      └── IntelXContractClient.get_market_intelligence()
   │             └── Sentiment, Confidence, Grounded Citations, Untrusted Text Sanitization
   │
   ├── Inference (http://inference:8000)
   │      └── InferenceContractClient.explain_decision()
   │             └── Multi-provider explanation, telemetry summaries (Fail-soft fallback)
   │
   ├── Sentinel (http://sentinel:8000)
   │      ├── SentinelContractClient.verify_action_authorization()
   │      └── SentinelContractClient.dispatch_emergency_halt()
   │
   └── Memora (http://memora:8000)
          └── MemoraScopedClient.record_scoped_event()
                 └── Scopes: PRIVATE (stratex), SHARED (mesh), UNIVERSAL (universe)
```

| Call Path | Source File & Line | Target Contract | Status | Invariants & Risk Bounds |
| :--- | :--- | :--- | :--- | :--- |
| **Futuris Forecast Advisory** | `autonomy/agent_contracts.py:70-170` | `FuturisContractClient.get_forecast()` | `MIGRATED`, `TESTED` | Rejects stale forecasts (> 300s). Invariant: Cannot place orders or widen risk stops. |
| **IntelX Market Intelligence** | `autonomy/agent_contracts.py:175-275` | `IntelXContractClient.get_market_intelligence()` | `MIGRATED`, `TESTED` | Validates citation integrity. Sanitizes text. Rejects ungrounded claims. |
| **Inference Decision Consultation** | `autonomy/agent_contracts.py:280-360` | `InferenceContractClient.explain_decision()` | `MIGRATED`, `TESTED` | 6s timeout with deterministic local fallback. Zero blocking on trading loop. |
| **Sentinel Policy Verification** | `autonomy/agent_contracts.py:365-425` | `SentinelContractClient.verify_action_authorization()` | `MIGRATED`, `TESTED` | Zero-trust capability check. Emergency halt routing. |
| **Memora Scoped Memory Feed** | `autonomy/agent_contracts.py:430-490`, `autonomy/memora_audit.py:40-160` | `MemoraScopedClient.record_scoped_event()` | `MIGRATED`, `TESTED` | Scopes: `PRIVATE`, `SHARED`, `UNIVERSAL`. Memory is never treated as broker truth. |
| **Agent Advisory Supervisor** | `autonomy/agent_contracts.py:495-585` | `AgentAdvisorySupervisor.evaluate_strategy_candidate()` | `MIGRATED`, `TESTED` | **HARD INVARIANT**: Size multiplier $\in [0.0, 1.0]$. May dampen/skip, but can NEVER enlarge trade size or relax risk. |

---

## 8. Rollout & Flag Policy Summary

| Configuration Flag | Current Value | Required Rollout State |
| :--- | :--- | :--- |
| `LIVE_TRADING_ENABLED` | `false` | **Permanently `false`**. Live-money execution is forbidden. |
| `TRADING_MODE` | `TESTNET` / `PAPER` | Default to `PAPER` / `TESTNET`. |
| `STRATEX_EXECUTION_MODE` | `DISABLED` | Set to `DEMO` only for validated demo runs. |
| `STRATEX_ORDER_MANAGER_ENABLED` | `false` | Default `false`; enabled when testing OMS pipeline. |
| `STRATEX_PORTFOLIO_RISK_ENABLED` | `false` | Default `false`; enabled for portfolio-wide gate testing. |
| `STRATEX_RECONCILIATION_ENABLED` | `false` | Default `false`; enabled when supervised worker is active. |
| `STRATEX_ALLOWED_EXECUTION_VENUES`| `binance` | Default `binance`; `mt5` added only with active bridge. |
