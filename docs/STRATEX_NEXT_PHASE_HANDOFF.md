# Stratex Upgrade Handoff — FRIDAY Universe

**Prepared:** 18 October 2026  
**Purpose:** Give a new ChatGPT session enough architectural and implementation context to continue Stratex without rebuilding working components or enabling unsafe trading.  
**Primary repository:** https://github.com/surendra2304/Stratex  
**Current base:** `main`  
**Previous implementation PR:** [#4 — Add Stratex agent catalog and Binance Demo environment isolation](https://github.com/surendra2304/Stratex/pull/4) — merged 17 October 2026.

## 1. The project context: FRIDAY Universe

The user's master guide describes FRIDAY Universe v2.2.0 as a nine-agent, microservice-oriented AI operating system under `D:\FRIDAY Universe`, hosted across OCI Always Free ARM64/AMD compute, with Docker network `friday_mesh` and Caddy TLS ingress at `https://friday-universe.duckdns.org`.

The nine agents are:

1. **FRIDAY** — user-facing voice/desktop assistant and master task orchestrator; internal service `http://friday:10000`.
2. **Inference** — multi-provider LLM gateway and deliberation service; `http://inference:8000`.
3. **Memora** — persistent episodic memory, knowledge graph and vector recall, with private/shared/universal memory scopes; `http://memora:8000`.
4. **Stratex** — strategy, risk and trading execution platform; `http://stratex:8000`.
5. **IntelX** — OSINT/research, source evidence and fact verification; `http://intelx:8000`.
6. **Futuris** — statistical forecasting, market regimes and calibrated probabilities; `http://futuris:8000`.
7. **Cortex** — web operations, dashboard and lead intelligence; `http://cortex:8000`.
8. **Forge** — isolated software synthesis, test generation and repair; `http://forge:8000`.
9. **Sentinel** — security, authorization, cryptographic signing, audit trail and kill switch; `http://sentinel:8000`.

The guide is a target architecture and operational description, not proof that every service or route is deployed and healthy. Confirm actual runtime configuration before changing infrastructure. Secrets remain isolated per service; never commit API keys, MT5 passwords, bridge tokens or OCI credentials.

### Intended trading information flow

- FRIDAY receives the user's intent and orchestrates specialists.
- IntelX may provide cited market/news evidence.
- Futuris may provide a calibrated regime/forecast and uncertainty.
- Inference may summarize or compare hypotheses, but is advisory only.
- Sentinel/policy enforcement checks authorization, risk limits, auditability and emergency stop state.
- Stratex independently validates the intent, calculates deterministic risk, checks current venue/account state, and submits only to an explicitly enabled demo venue.
- Memora stores scoped decision context and trade outcomes; it must not be the source of truth for live broker order state.
- FRIDAY reports confirmed facts and labels uncertainty. LLMs must never approve risk exceptions, fabricate fills, or bypass the kill switch.

## 2. What Stratex should become

Do **not** build a separate Binance bot and a separate MT5 bot. Keep Stratex as one strategy and trading platform with a common core and venue-specific adapters.

### Target components

1. **Strategy engine** — retain and improve existing strategies (RSI, Bollinger Bands, EMA, ADX, SuperTrend, VWAP, Donchian and multi-timeframe strategies). A strategy proposes an intent; it does not call broker APIs directly.
2. **Normalized market data** — candles, quotes, instrument metadata and health, with source timestamps and freshness checks. Do not assume symbols or contract conventions are the same across venues.
3. **Trade intent contract** — normalized venue, environment, symbol, side, order type, requested size, stop, take-profit, strategy/reason, stable client order ID, and trace/audit metadata.
4. **Deterministic risk/policy** — account equity, per-trade stop-loss risk, margin, total exposure, correlated exposure, daily loss, drawdown, stale-data limits and kill-switch checks. Fail closed on missing or inconsistent data.
5. **Order-management system (OMS)** — durable intent state machine, idempotency, submission, partial fills, cancel/replace, protective orders, restart recovery and venue reconciliation. An ambiguous timeout is `UNKNOWN`, never permission to blindly retry.
6. **Venue adapters** — Binance USDⓈ-M Futures Demo and IC Markets MT5 Demo first; later add other supported Binance products/venues only through independent adapters and product-specific tests.
7. **Portfolio and ledger** — broker-native positions/tickets, native currencies, realized/unrealized PnL, fees, swaps/funding, margin and exposure. Never sum USD, USDT, EUR or CFD lots as if their units were interchangeable.
8. **Monitoring/reconciliation** — health, stale quotes, broker disconnects, order/protection state, drift between local and broker ledger, retries and alerting.
9. **FRIDAY integrations** — read-only/advisory integrations to Inference, IntelX, Futuris and Memora, plus explicit Sentinel authorization/audit. Agent outputs can inform a decision but cannot override deterministic risk limits.

### Venue boundary

- **Binance Futures Demo** is the first crypto execution target. The user created Binance Demo Trading API credentials. Keep withdrawal/transfer permissions disabled and never request credentials in chat. Use the Demo endpoint/credentials appropriate to USDⓈ-M Futures; do not silently fall back to Spot Testnet, Futures Testnet, or production.
- **IC Markets MT5 Demo** is already configured on desktop as a demo account (server previously shown as `ICMarketsSC-Demo`, hedge mode, USD demo balance). Preserve hedge-mode ticket semantics: multiple positions and opposite-side positions can coexist. Never treat an MT5 position as a single net Binance position.
- MT5's Python package normally talks to an installed MT5 terminal, commonly on Windows. For laptop-off operation, Stratex core can run in OCI and an authenticated MT5 bridge/EA can run on a suitable always-on Windows host/VPS. Do not assume an ARM Linux container can host the native terminal.
- Use only demo environments until the full validation gates are met. The presence of an API key, a green unit test, or an LLM recommendation is not permission for live trading.

## 3. Repository implementation already present

PR #4 is merged into `main`. It established the initial architecture/venue-isolation foundation. Additional implementation visible on the current `main` includes the following modules and tests; inspect the current code before changing them.

### Existing OMS / intent foundation

- `stratex_agents/order_intent.py` — normalized trade intent.
- `stratex_agents/order_store.py` — durable SQLite intent state and idempotency/recovery queries.
- `stratex_agents/oms.py` and `stratex_agents/runtime.py` — order manager and runtime wiring.
- `stratex_agents/venue/binance_futures_oms_adapter.py` — Binance Futures order path, market filters, position mode and protective conditional orders.
- `exchanges/mt5_bridge_adapter.py` and `mt5_bridge/server.py` — authenticated MT5 demo bridge, persistent client-order records, order lookup and ticket-based operations.
- `stratex_agents/venue/mt5_agent.py` — sanitized MT5 health and broker-aware risk evaluation.
- `stratex_agents/risk_agent.py` — deterministic venue-aware risk wrapper. Binance uses the existing RiskGate; MT5 is denied unless a broker-aware evaluator is explicitly supplied.
- `stratex_agents/portfolio_agent.py` — read-only multi-venue snapshot. Keeps native currency and position metadata explicit; does not invent FX conversions.
- `stratex_agents/reconciliation_agent.py` — read-only evidence-based state reconciler. It updates state only when a venue resolver explicitly confirms an order status.
- `stratex_agents/registry.py` — agent catalog/status metadata.
- `execution.py` — opt-in OMS route, with legacy execution retained for compatibility. The new path should remain disabled by default until end-to-end tests pass.

### MT5 risk checks

The MT5 bridge contains broker-calculated sizing and preflight using MetaTrader functions such as `order_calc_profit`, `order_calc_margin`, and `order_check`. The documented defaults are `MT5_MAX_RISK_PER_TRADE=0.005` and `MT5_MAX_MARGIN_FRACTION=0.20`, with code-side ceilings. These are initial per-entry guardrails, not a complete portfolio-level risk system. Verify the formulas, broker semantics, symbol settings, and test assumptions before relying on them.

### Tests added/expanded

Look for:
- `tests/test_risk_agent.py`
- `tests/test_portfolio_agent.py`
- `tests/test_reconciliation_agent.py`
- `tests/test_mt5_bridge_server.py`
- `tests/test_mt5_bridge_adapter.py`
- `tests/test_mt5_venue_agent.py`
- `tests/test_binance_futures_oms_adapter.py`
- `tests/test_stratex_runtime_factory.py`
- `tests/test_stratex_oms.py`

Tests with fakes verify contracts and fail-closed behavior; they are not proof that the real Binance or IC Markets demo accounts work end to end.

## 4. Current repository/PR status

- Repository: https://github.com/surendra2304/Stratex
- Previous implementation PR #4 is **merged**. Do not attempt to reopen it or claim its code is still awaiting review.
- The feature branch `feat/stratex-agent-orchestration` is now behind `main`; the current `main` includes the implementation. The latest observed GitHub Actions run for main was **successful** at commit `bb79c011522c2739cb2548be1ae41e70f08087f5` on 17 October 2026. Re-check CI for the current HEAD before merging future work.
- This handoff roadmap is being proposed separately so a new chat can find the intended next phases. It does not mean all future tasks are complete.
- No production deployment, OCI changes, or real-account trading was performed as part of this handoff.

## 5. Required work remaining — execute in this order

### Phase A — baseline and integration map
1. Fetch current `main`; review `git log`, working tree, CI, Docker build and all relevant files.
2. Map legacy `bot.py` strategy entry/exit, trailing stop, closing, fees, position accounting and trade ledger. Identify every direct venue call.
3. Build a strategy-to-intent adapter so existing strategies generate intents through the shared contract. Do not duplicate strategies or silently change their signals.
4. Wire the canonical deterministic risk/policy stack into OMS at the orchestration boundary. Confirm that LLM responses cannot approve execution.

**Acceptance:** written map of every entry/exit path, no accidental duplicate execution path, regression tests pass, OMS remains disabled by default.

### Phase B — complete Binance USDⓈ-M Futures Demo lifecycle
1. Verify the current Demo Trading API endpoint and client-library compatibility from official current documentation.
2. Ensure the selected environment is explicit and impossible to silently downgrade/fallback to another environment.
3. Route strategy entries through the OMS with deterministic risk and idempotent client IDs.
4. Handle partial fills, exchange precision/minimum notional, leverage/margin mode, hedge/one-way mode and reduce-only semantics.
5. Place and verify stop-loss/take-profit protection; reconcile the entry and both conditional Algo Orders.
6. When one protective order triggers, cancel/reconcile its sibling and reconcile actual position state. Do not allow repeated entries while a prior entry/exit/protection state is unresolved.
7. Add bounded retries only for safe idempotent reads; ambiguous order submissions must be reconciled, not replayed.
8. Add demo integration tests and a soak test covering disconnects, process restarts, partial fills and missing protection.

**Acceptance:** all fills/positions/protection match Binance Demo; unknown states halt new entries; no production endpoint can be reached by demo config; CI green.

### Phase C — complete IC Markets MT5 Demo lifecycle
1. Validate the actual connected demo server/account mode on every startup and before submission. Redact account IDs and credentials in logs.
2. Obtain broker-native symbol info/tick freshness, contract size, tick size/value, volume min/max/step, trade mode, stop levels, filling/execution modes, margin and account currency.
3. Validate stop-loss and take-profit orientation for BUY/SELL; use broker calculations for risk and margin. Reject unsupported/ambiguous instruments.
4. Preserve hedge-mode positions by ticket; test partial fills, rejection, close-by-ticket, pending orders, modifications and opposing positions.
5. Reconcile broker history, deals, commission, swap, realized/unrealized PnL and persistent bridge order records after restart/disconnect.
6. Exercise terminal loss/reconnect and stale quote behavior; unknown order state must block duplicates.
7. Ensure the bridge is private-network-only, authenticated, rate-limited, and protected by a rotated strong token.

**Acceptance:** repeatable IC Markets demo end-to-end tests, no public bridge exposure, account-currency risk correct for several instrument types, restart reconciliation proven.

### Phase D — shared portfolio, account risk and kill switch
1. Add portfolio-level maximum open risk/exposure, daily loss, max drawdown, leverage/margin utilization and correlated-exposure limits.
2. Include both venues without conflating native quantities/currencies. FX conversions must carry explicit rate, source and timestamp; stale/missing FX means degraded snapshot and conservative denial.
3. Persist order/position/fill/fee/funding/swap events and reconcile to venue truth.
4. Implement an independent global kill switch and per-venue circuit breakers. On risk breach or repeated data/venue failure: stop new entries, preserve protective exits, alert and require explicit recovery policy.
5. Confirm order state transitions are legal and durable, including ACCEPTED, PARTIALLY_FILLED, FILLED, CANCELLED, REJECTED and UNKNOWN.

**Acceptance:** cross-venue risk tests, kill-switch tests, ledger reconciliation and restart recovery pass.

### Phase E — agent integrations
- **Futuris:** accept structured forecasts with timestamps, horizon, calibrated uncertainty and source metadata; only influence risk conservatively within hard bounds.
- **IntelX:** pass evidence/citations and sentiment features; never let web text inject commands into the trading path.
- **Inference:** advisory explanation/strategy comparison only; no execution authority.
- **Sentinel:** authenticate capability-scoped requests, record tamper-evident audit events and provide an emergency stop.
- **Memora:** store strategy rationale, decisions, risk outcomes and post-trade summaries with private/shared scopes; do not use memory as broker truth.
- **FRIDAY:** route a request and present confirmed status, not claim orders succeeded until venue reconciliation proves it.

**Acceptance:** contract tests for timeouts, malformed/untrusted payloads, permissions, audit logging and graceful degradation. If an agent is unavailable, Stratex should remain safe and should not invent the missing signal.

### Phase F — cloud deployment and observability
1. Keep strategy/OMS core separate from the MT5 terminal runtime.
2. Check ARM64 vs AMD64 package/container compatibility; do not assume all dependencies support both architectures.
3. Use OCI private networking/firewall rules, secrets manager/environment injection, least privilege and no credentials in Git or logs.
4. Add structured logs, correlation IDs, readiness/liveness, metrics, alerting, backups and restore drills.
5. Deploy through the existing FRIDAY mesh only after local/demo acceptance. Never deploy merely because CI passed.
6. Verify Caddy routes and service health against actual deployment, not only the master guide.

**Acceptance:** repeatable deployment, secret scan, backup/restore evidence, documented rollback and an observed demo soak test.

## 6. Non-negotiable safety rules

- Keep `STRATEX_EXECUTION_MODE=DISABLED` until all acceptance gates are met.
- Keep live trading blocked; never switch to live as a shortcut for testing.
- Keep MT5 out of the default venue allowlist until its full demo lifecycle is proven.
- Binance withdrawals/transfers stay disabled. Never paste API keys or secrets into chat or commits.
- Do not expose the MT5 bridge publicly. Use authenticated private connectivity and rotate bridge tokens.
- Missing/stale quotes, missing risk data, missing protective orders, mismatched environment, unknown order state, or unavailable risk service => deny new entry.
- No blind retry after an ambiguous submit; reconcile first.
- No automatic failover of an order between venues.
- LLMs and agent narratives are never an authorization authority.
- Never claim a demo soak test or real-account validation was performed unless it actually was.

## 7. How the next chat should resume

Start by reading this document and the current repository `main`, then:
1. Verify latest CI and current branch/PR state.
2. Inspect the exact implementation and tests named above rather than trusting this summary blindly.
3. Produce a concise implementation matrix: `existing / incomplete / missing / tested / integration-tested`.
4. Continue Phase A, especially mapping legacy entry/exit/ledger code into the OMS.
5. Work in a dedicated feature branch and raise focused PRs with clear acceptance criteria. Do not merge or deploy until checks and demo validation justify it.
6. Ask the user only for truly missing information when it blocks a concrete decision; never request secrets. The user authorized direct repository work, but that is not authorization to enable live-money execution.

## Useful links

- Stratex repo: https://github.com/surendra2304/Stratex
- Merged implementation PR #4: https://github.com/surendra2304/Stratex/pull/4
- FRIDAY Universe guide file: `FRIDAY_UNIVERSE_MASTER_GUIDE.md` (uploaded by user in the previous chat)
- MetaTrader Python API reference:
  - https://www.mql5.com/en/docs/python_metatrader5/mt5ordercalcprofit_py
  - https://www.mql5.com/en/docs/python_metatrader5/mt5ordercalcmargin_py
  - https://www.mql5.com/en/docs/python_metatrader5/mt5ordercheck_py


---

## 8. Continuation checkpoint update — 18 October 2026, later session

This section supersedes any earlier assumption that the feature branch contains unmerged implementation code.

### Pull request and branch reality

- The feature implementation branch `feat/stratex-agent-orchestration` is now **behind `main` with no unique commits**. The implementation that was reviewed in this session is already visible on `main`; do not open a duplicate code PR from that branch.
- PR #4 is closed/merged and should be treated as historical implementation work.
- PR #9 is the current open documentation PR: https://github.com/surendra2304/Stratex/pull/9. This handoff update belongs to that documentation PR.
- At the last status check, CI run `39097438524` for commit `ca5672a867cede0630e0530481a7ba44fd6c7e1c` was still **in progress**. Linting passed; pytest, PostgreSQL OMS integration, and Docker build stages had not yet reported final results. An earlier run at `8c34f04299b3b95a617f013e995cb1ee750eb496` was successful. Re-check current checks before treating the latest code as validated.

### Additional code paths present on current main

- `stratex_agents/risk_agent.py`: deterministic wrapper around the existing Binance risk gate; missing context/policy denies. MT5 denies unless an explicit broker-aware evaluator is configured.
- `stratex_agents/portfolio_agent.py`: read-only cross-venue snapshot; retains native currency and position identity, marks missing FX conversions unavailable, and surfaces venue failures as degraded.
- `stratex_agents/reconciliation_agent.py`: evidence-based reconciliation engine. It can update persisted intent status only when an injected venue resolver explicitly confirms state; it has no order-submission authority.
- `stratex_agents/venue/binance_futures_oms_adapter.py`: includes a read-only reconciliation path that checks entry state and verifies both protective Algo Orders before considering a fill fully protected.
- `exchanges/mt5_bridge_adapter.py`: includes a read-only reconciliation path based on the persistent bridge client-order record.
- `stratex_agents/runtime.py`: exposes a reconciliation-agent factory from adapters that implement `reconcile_intent`; it does not yet mean a supervised recurring worker is deployed.
- `mt5_bridge/server.py`: includes broker-calculated MT5 lot sizing and per-entry risk/margin preflight using terminal APIs, plus `order_check` before submission.
- Tests for risk agent, portfolio, reconciliation, MT5 broker sizing, MT5 adapter/agent, Binance protective-order reconciliation and injected MT5 demo health are present. Confirm all test names and run outcomes from the live tree and latest CI.

### Important status wording

These are **implementation foundations**, not proof of a finished live trading lifecycle. In particular, the portfolio/reconciliation agents are not automatically scheduled simply because their classes exist. Binance Demo and IC Markets Demo need real, authenticated end-to-end validation. No OCI deployment or demo soak test is claimed. Keep new entries disabled until the complete lifecycle and recovery matrix passes.

### Immediate next work

1. Refresh main and check latest CI status/result.
2. Create a file-by-file integration matrix for current strategy entries, exits, trailing stops, closes, protective-order changes and trade ledger. Identify direct legacy exchange calls.
3. Route the existing strategy runner through a single OMS path under a default-off feature flag. Avoid duplicate legacy and OMS execution.
4. Wire a supervised reconciliation worker with explicit lifecycle/health controls and safe scheduling; verify terminal-state transitions against `OrderIntentStore`.
5. Finish Binance USDⓈ-M Futures Demo order lifecycle (partial fills, conditional Algo Order semantics, sibling cancellation, position reconciliation and restart recovery).
6. Finish MT5 ticket-aware lifecycle (hedge mode, pending orders, modifications, close-by-ticket, history/deals, commission/swap and restart recovery).
7. Add shared portfolio risk, daily loss/drawdown limits, global kill switch and per-venue circuit breakers.
8. Only then integrate FRIDAY/Inference/Memora/IntelX/Futuris/Sentinel contracts and deploy through OCI after a demo soak test.

**Do not merge PR #9 as though it implements the remaining code.** It is a documentation/handoff PR. The next implementation PR must be based on current main, include focused tests, and keep execution disabled by default.
