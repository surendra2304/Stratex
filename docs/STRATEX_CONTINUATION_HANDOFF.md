# Stratex Upgrade and Continuation Handoff
**Prepared:** 18 October 2026  
**Purpose:** Give a new ChatGPT conversation the architecture, code status, safety gates, and exact implementation sequence for continuing Stratex.

## 1. Read this first

This is a continuation brief, not a claim that multi-venue autonomous execution is finished. It records the target architecture, implemented foundations, validation gaps, deployment assumptions and next steps.

**Do not enable autonomous execution merely because environment variables exist.** Keep the OMS and reconciliation flags disabled until the full strategy-to-venue order lifecycle has passed CI and demo integration tests. Never ask the user to paste API secrets, MT5 passwords, bridge tokens or OCI credentials into chat; use private deployment secrets.

## 2. Product intent

Stratex should become one multi-market trading platform, not separate unrelated bots. Preserve the current strategy engine, indicators, paper lab, trade state, risk controls and dashboard where practical. Introduce venue adapters so Binance Futures and IC Markets MT5 can be selected under one orchestration, risk and order lifecycle.

The user's objectives:
- Binance Futures Demo Trading as the initial crypto target.
- IC Markets MT5 Demo as a second venue, supporting Forex and whichever metals, indices, equities or other CFDs the demo account actually lists.
- Stratex should run continuously without requiring the user's laptop to stay powered on.
- Host Stratex Core and the FRIDAY agent ecosystem with OCI/Dokploy.
- Add bounded specialist agents around the core.
- Continue directly through GitHub PRs and CI, without depending on Antigravity for source editing.

Do not assume every instrument exists on each venue. Query each venue for current tradable instruments and contract rules. Preserve broker-native symbol names and sizing units.

## 3. FRIDAY Universe context from the supplied documents

The supplied FRIDAY Universe Master Guide and Master Specification describe nine cooperating services on an authenticated private Docker mesh called friday_mesh. Stratex is the trading domain specialist: it may consume context from other agents but must keep independent trading-risk and execution authorization.

| FRIDAY agent | Intended Stratex relationship |
| --- | --- |
| FRIDAY | Accepts user intent, orchestrates and reports. It must not bypass Stratex risk controls. |
| Inference | Optional LLM deliberation and summaries. Advice only; no order permission. |
| Memora | Long-term memory and audit/context storage under private/shared/universal namespace rules. Store trade events without storing venue secrets. |
| IntelX | Source-backed news/OSINT context. Evidence and uncertainty, not trade commands. |
| Futuris | Probabilistic forecast/regime context. Advisory only; never directly place or size trades. |
| Sentinel | Security/authorization and audit gate. Can veto, must not be weakened to make a trade pass. |
| Cortex | Dashboards and telemetry, where appropriate. |
| Forge | Controlled software synthesis/testing for future development workflows. |
| Stratex | Owns market adapters, validated strategies, deterministic risk, portfolio/order lifecycle and trade records. |

The Master Guide lists Stratex on internal port 8000, host port 8008 and a public /stratex route; the current Compose file maps 8008:8000. Verify the real running deployment before changing routes. The source documents call for private mesh service communication and Caddy TLS ingress; do not expose specialist APIs or databases directly to the internet.

## 4. Target architecture

    FRIDAY / Dashboard / API clients
                 |
           Stratex Core
                 |
        Validated strategy signals
                 |
      Deterministic Risk & Policy Gate <--- kill switch / Sentinel policy
                 |
          Normalized TradeIntent
                 |
       Durable Order Management (OMS)
          |                    |
    Binance Futures       IC Markets MT5
    Venue Adapter         Private Windows Bridge
          |                    |
    Binance Demo API      MT5 Demo Terminal
          \                    /
             Venue reconciliation
                      |
             Position & Portfolio state
                      |
             Audit / Memora / metrics

Inference, Futuris and IntelX provide advisory context only. No LLM or external agent can authorize a trade.

### Main components

1. **Stratex Core Orchestrator:** chooses explicit venue, coordinates data/strategy/risk/order lifecycle and reports truthful state.
2. **Market Data Agent:** lists instruments, normalizes quotes/candles, records venue/environment/timestamps/freshness and rejects invalid or missing data.
3. **Strategy Supervisor:** reuses the existing strategy registry; only validated strategies can produce eligible intents.
4. **Deterministic Risk & Authorization Agent:** reuses the existing RiskGate and ExecutionPolicy. Missing/stale inputs, calculation errors, policy denials and kill-switch errors fail closed.
5. **Venue agents:** Binance Futures and MT5 own API calls, credential handling, symbol/contract constraints, order statuses and position identifiers.
6. **Durable OMS:** persists intent before submission, assigns stable client ID, models reserved/submitting/accepted/partial/filled/rejected/cancelled/unknown states, prohibits blind retry after ambiguous submission and does not fail over automatically between brokers.
7. **Reconciliation Agent/Worker:** read-only polling based on explicit venue evidence. A missing order lookup is not evidence that an order was cancelled.
8. **Portfolio & Exposure Agent:** reports balances and positions per venue; never equates crypto quantity with MT5 lots; converts currencies only with explicit rates.
9. **Audit/Monitoring:** records mode, intent, signal source, risk decision, API outcome, fills, protective order IDs, rejections, reconciliation evidence and kill-switch state. Secrets must never be logged.

## 5. Non-negotiable safety invariants

- LIVE_TRADING_ENABLED=False stays enforced; no production key or endpoint is allowed in this rollout.
- Safe defaults remain TRADING_MODE=PAPER, STRATEX_EXECUTION_MODE=DISABLED, STRATEX_ORDER_MANAGER_ENABLED=false and STRATEX_RECONCILIATION_ENABLED=false.
- Binance Demo and legacy Testnet credentials are distinct. Current Binance Demo uses BINANCE_API_KEY and BINANCE_API_SECRET; never silently substitute legacy Testnet keys.
- Keep transfers/withdrawals unavailable. Grant only the permissions required for demo access and orders.
- Risk decisions are deterministic. FRIDAY, Inference, Futuris, IntelX, model outputs or tool results cannot authorize orders or override limits.
- New entries require stop-loss and take-profit protection. An entry without verified protection is not a successful protected trade.
- If only one protective order was acknowledged, preserve it until exposure is confirmed closed; do not cancel it while the position may still be open.
- On timeout/ambiguous submit, query the venue and reconcile before retrying; never retry with a new order ID to “try again.”
- Reconcile existing Binance positions/orders before new entries; refuse if existing exposure is unexplained.
- In MT5 Hedge Mode, preserve position tickets and never close unrelated positions by symbol alone.
- MT5 sizing must use broker contract details/calculations, not Binance formulas.
- Never expose the MT5 bridge to the public internet. Use loopback/private VPN addressing and authenticated requests.
- Persist OMS/idempotency state across Docker redeploys; runtime databases must not be committed.

## 6. What exists in the repository

PR #4 was merged in GitHub. Repository: https://github.com/surendra2304/Stratex  
PR #4: https://github.com/surendra2304/Stratex/pull/4  
Reported merge commit: a34d8061747774cc0befe131929f9af5a2a1b288

| Path | Present purpose / status |
| --- | --- |
| stratex_agents/registry.py | Credential-free agent catalog. Catalog configuration does not prove that a venue is healthy or order-capable. |
| stratex_agents/order_intent.py | Normalized trade intent and stable client-ID validation. |
| stratex_agents/order_store.py | Durable SQLite intent ledger, duplicate protection and unresolved-state tracking. |
| stratex_agents/oms.py | Venue allowlist, explicit demo checks, kill-switch/risk callback and no blind retry on ambiguous outcomes. |
| stratex_agents/runtime.py | Explicit OMS construction and read-only reconciliation-agent factory. |
| stratex_agents/venue/binance_futures_agent.py | Binance endpoint/credential selection; current Demo and legacy Testnet paths isolated. |
| stratex_agents/venue/binance_futures_oms_adapter.py | USDⓈ-M Futures market-entry foundation for Demo/Testnet, position-mode/symbol checks, isolated margin/leverage setup, protective exits and reconciliation hooks. Requires demo validation and full sibling-exit lifecycle handling. |
| stratex_agents/venue/binance_futures_data_adapter.py | Read-only Futures instrument/quote/candle data on selected non-production environment. |
| stratex_agents/market_data.py | Venue-neutral normalization preserving exact MT5 symbols. |
| stratex_agents/risk_agent.py | Deterministic venue-aware authorization wrapper. MT5 denied unless a broker-aware evaluator exists. |
| stratex_agents/portfolio_agent.py | Read-only balances/positions; explicit currency conversion only. |
| stratex_agents/reconciliation_agent.py | Evidence-based state updates through explicit venue resolvers; never submits orders. |
| scripts/reconciliation_worker.py | Opt-in polling worker, gated by OMS/reconciliation flags and demo settings. |
| mt5_bridge/server.py | MT5 bridge API: demo checks, symbol discovery, protected entries, idempotency lookup, sizing/preflight. |
| mt5_bridge/idempotency.py | Persistent MT5 client-order ledger against duplicate submissions. |
| exchanges/mt5_bridge_adapter.py | Stratex client adapter for private MT5 bridge, broker sizing and reconciliation. |
| dashboard.py | Agent catalog and authenticated read-only market instrument/quote/candle routes. |
| docker-compose.yml | PAPER default, live flag forced false, named volume for OMS state. Check Dokploy environment precedence on actual deployment. |
| tests/ | Tests for credential/environment isolation, idempotency, MT5 preflight, market-data normalization, risk, portfolio and reconciliation. |

These modules are foundations, not proof of end-to-end autonomous multi-market trading. Some legacy strategy, trailing, close and ledger paths may remain outside the shared OMS. Inspect all entry and exit call paths before enabling flags.

## 7. CI and PR snapshot

At the most recent inspection, PR #4 was closed and merged. GitHub Actions on main showed:
- Stratex Paper Shadow: successful.
- Stratex Strategy Runtime Truth: successful.
- Stratex CI/CD: failed in pytest after lint passed.
- Full pytest summary: 1,912 passed, 2 skipped, 1 failed.
- The single failure was an assertion-text mismatch in tests/test_reconciliation_worker.py: the test expected TESTNET_ENABLED=true while the worker returned semantically similar but differently worded text.

A follow-up branch named docs/stratex-handoff-and-ci-fix was created from the merge commit. It changes the worker message to the actionable/test-compatible text “Set TESTNET_ENABLED=true for demo/testnet reconciliation”. Re-run CI after opening the follow-up PR. Do not claim that CI passes until that run has completed.

## 8. Ordered execution plan

### Phase A - Get baseline clean
1. Open/review the follow-up PR with the CI assertion fix and this handoff.
2. Run lint, full pytest and Docker build. Fix every failure.
3. Review the exact diff; check for credentials, account identifiers and production URLs.
4. Keep all trading flags disabled while CI runs.

### Phase B - Finish Binance Demo lifecycle
1. Verify Demo endpoint selection and python-binance signatures against the locked project dependency.
2. Confirm fresh Demo credentials are configured privately; never put them in source, issues, PDF or chat.
3. Run read-only checks first: environment, account, position mode, instruments, filters and margin settings.
4. In an isolated demo test, place the smallest allowed market order and verify both protective Algo Orders independently.
5. Implement exit-order supervision: after one exit fills, cancel its sibling; reconcile positions, orders and fills after restarts/disconnects. If state is ambiguous, freeze new entries and alert.
6. Test timeout-after-accept, partial/no fill, rejection, filter changes, tick rounding, zero/insufficient margin, existing position, close failure and restart recovery.
7. Only after all checks pass, consider enabling the OMS flag in a private demo deployment. Never enable live money.

### Phase C - Finish MT5 Demo bridge
1. Determine whether an always-on Windows host exists. Prior discussion noted an IC Markets demo account on ICMarketsSC-Demo and Hedge Mode; verify current facts in terminal before relying on them.
2. Run MT5 terminal and bridge on the same Windows host, logged into the demo account.
3. Connect Stratex/bridge over a private VPN. Do not publish bridge port 8765 to the internet.
4. Verify authenticated health explicitly confirms demo mode; test a live-account negative case.
5. Query /v1/symbols and save exact symbol/contract data: contract size, digits, tick size/value, min/max/step volume, stop distance, currency and trade mode.
6. Test broker-aware sizing using MT5 order_calc_profit, order_calc_margin and order_check, plus attached SL/TP. Enforce limits on terminal side and in core policy.
7. Complete ticket/fill/partial-fill/pending-order/close-by-ticket/commission/swap/restart/ambiguous-timeout tests.
8. Keep MT5 out of the allowlist until a documented demo soak test passes.

### Phase D - Migrate all strategy/order paths to OMS
1. Inventory every legacy entry, SL/TP, trailing, cancel, close, reconciliation, ledger and panic path.
2. Define one normalized interface for entry, cancel/amend protection, close by ticket/position, orders, fills, positions and account risk.
3. Migrate one path at a time; never execute a single signal in both legacy and OMS paths.
4. Persist idempotency through retry and process restart; validate state transitions from venue evidence.
5. Enable OMS entry routing only after monitoring/trailing/closing/ledger all understand OMS states and venue-specific IDs.

### Phase E - Portfolio, limits and advisory agents
1. Aggregate balances/positions/PnL without mixing units or assuming FX conversions.
2. Add account/venue limits, risk per trade, daily loss/drawdown caps, max concurrent positions and kill switch.
3. Define advisory schemas for Futuris, IntelX and Inference: source, timestamp, confidence and expiry.
4. Missing, stale, contradictory or malformed advisory context should cause hold or be ignored; it must never override deterministic policy.
5. Store Memora events for intent/risk/order/protection/reconciliation without credentials.
6. Dashboard must distinguish configured, authenticated, demo-confirmed, order-capable and reconciliation-healthy.

### Phase F - OCI/Dokploy deployment
1. The user reported an active AMD64 Oracle instance around 1 GB RAM and 50 GB disk. The supplied master specification separately targets ARM64 A1 at 2 OCPUs, 12 GB RAM and 150 GB boot disk. These are different targets; confirm which runs Stratex before sizing/moving services.
2. Verify Dokploy/Compose variable precedence so PAPER is the actual startup mode and workers remain disabled by default.
3. Confirm named volume stratex_order_state preserves /app/state across redeploys and backups.
4. Verify health checks, restarts, log redaction, outbound API connectivity, disk alerts and CPU/RAM budgets.
5. Do not increase storage blindly. Inspect filesystem, Docker image/layers, logs, SQLite/WAL and retention requirements first.
6. Do a redeploy with all trading flags disabled, run read-only smoke tests, and verify ledger persistence. Deploy the MT5 bridge separately.

## 9. Private environment checklist

| Variable | Safe default / requirement |
| --- | --- |
| TRADING_MODE | PAPER |
| LIVE_TRADING_ENABLED | False, enforced |
| TESTNET_ENABLED | False by default; reconciliation worker requires true for Binance demo/testnet |
| BINANCE_DEMO_TRADING | false until private demo setup and tests are ready |
| BINANCE_API_KEY / BINANCE_API_SECRET | Current Demo Trading keys only; configure privately |
| API_KEY / SECRET_KEY | Separate legacy Testnet credentials only |
| STRATEX_EXECUTION_MODE | DISABLED |
| STRATEX_ORDER_MANAGER_ENABLED | false |
| STRATEX_RECONCILIATION_ENABLED | false |
| STRATEX_ALLOWED_EXECUTION_VENUES | binance as default allowlist; global execution still disabled |
| STRATEX_ORDER_DB | state/stratex_orders.sqlite3 on persistent storage |
| MT5_BRIDGE_ENABLED | false |
| MT5_REQUIRE_DEMO | true |
| MT5_BRIDGE_URL | Private/VPN-only; never public |
| MT5_BRIDGE_TOKEN | High-entropy private secret |
| MT5_MAX_RISK_PER_TRADE | 0.005 default; code enforces bounds |
| MT5_MAX_MARGIN_FRACTION | 0.20 default; code enforces bounds |

Never ask the user to paste credentials. Ask them to set secrets in private deployment settings and share only variable names/configuration confirmation and non-sensitive diagnostic output.

## 10. Integrations to request only when needed

GitHub is already connected and has been used to inspect source, create branches, edit files and manage PRs. Do not ask the user to reconnect GitHub unless it stops working.

No dedicated Dokploy/OCI deployment connector was found in the tool/plugin search at the time of handoff. Before deploying, request the least-privilege route:
- Dokploy API URL plus token stored in an approved secret mechanism, or a GitHub Actions deployment secret/webhook configured by the user.
- SSH/OCI access only if an available supported tool requires it; otherwise use a reviewed Actions workflow or give the user exact commands.
Never claim OCI/Dokploy access unless a connected tool actually verified it.

For MT5, ask whether an always-on Windows VPS/host is available. If yes, configure the private bridge there. If no, discuss a suitable Windows host; do not assume a Linux/ARM Oracle container can reliably run the official MT5 desktop terminal.

## 11. PR discipline

- Create a dedicated branch from current main for each logical phase.
- Each PR should list changed files, safety impact, test evidence, known gaps and deployment steps.
- Read logs from failed CI steps; do not rely only on a badge.
- Do not merge with known failed CI unless the user explicitly accepts the exception after seeing the risk. The correct path here is to fix the message/test mismatch.
- PR #4 is already merged; do not reopen it. Continue on the follow-up branch created from the merged commit.

## 12. New-chat starter prompt

Copy the following prompt into a new ChatGPT conversation and attach this PDF.

> Continue work on my Stratex repository: https://github.com/surendra2304/Stratex. Read the attached Stratex Upgrade and Continuation Handoff fully before changing code. GitHub is already connected; do not ask me to reconnect it.
>
> First inspect current main, the continuation PR, and GitHub Actions. Verify whether the CI message fix passes, then inspect the PR diff and tests. Work in logical PRs rather than commits to main. Keep all live-money trading disabled and all OMS/reconciliation flags disabled until demo integration tests pass.
>
> Stratex must remain one multi-venue platform: preserve existing strategies; build venue-specific Binance Futures Demo and IC Markets MT5 Demo adapters; use shared normalized intents/OMS, persistent idempotency, deterministic risk gates, venue-evidence reconciliation, venue-aware portfolio state and audit. AI agents are advisory-only. Never paste secrets into chat or commits.
>
> Continue in this order: clean CI; finish Binance entry/protection/sibling-exit reconciliation; finish MT5 Windows bridge risk sizing and reconciliation; migrate every legacy strategy entry/trailing/close/ledger path through OMS; then controlled OCI/Dokploy demo deployment. Ask one concise question only when an actual external dependency blocks the next task (for example whether a Windows MT5 host exists). Do not request every credential upfront.
>
> Keep the handoff updated as work progresses. Create meaningful PRs and do not claim deployment or demo-account validation unless it actually happened.

## 13. Source documents and locations

- Supplied FRIDAY Universe Master Guide: nine-agent mission, mesh endpoints, Stratex Binance Futures/Paper role, Memora/Sentinel integration.
- Supplied FRIDAY Universe Master Specification: evidence-over-confidence, non-bypassable domain safety, private mesh, Stratex risk invariants and deployment target/resource budget.
- Stratex architecture design: docs/STRATEX_AGENT_ARCHITECTURE.md.
- Main repository: https://github.com/surendra2304/Stratex
- PR #4 (merged): https://github.com/surendra2304/Stratex/pull/4

**Definition of complete:** Stratex is not done when adapter classes exist. It is done only when existing strategy decisions traverse deterministic risk -> persisted OMS -> explicitly selected venue -> venue-confirmed protection -> reconciliation -> portfolio/ledger update, with automated failure/restart tests and a verified demo deployment.
