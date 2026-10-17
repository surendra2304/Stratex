# Stratex Upgrade Handoff — 2026-10-18

## Purpose and product decision
Repository: https://github.com/surendra2304/Stratex
The current main includes merged PR #4, “Add Stratex agent catalog and Binance Demo environment isolation”. Always compare branches against main before creating a PR; the old feature branch may already be merged or behind.

Keep Binance and MetaTrader 5 under **one Stratex platform**, not separate trading products/repos. Venue-specific agents/adapters live inside Stratex. The shared core owns normalized trade intents, deterministic risk authorization, durable order state, reconciliation, portfolio reporting, observability and kill-switch semantics. Binance Futures and MT5 remain independent execution venues with different contract/quantity semantics.

## Scope
1. Binance USDⓈ-M Futures Demo first.
2. IC Markets MT5 demo as the first Forex/CFD venue.
3. Binance Spot and COIN-M Futures later, each with product-specific adapter/tests.
4. Do not build Quotex/Pocket Option integration unless an official API and authorized test environment are verified.

## Target architecture
- **Core Orchestrator:** routes work and owns lifecycle, but cannot bypass policy.
- **Market Data Agent:** venue-aware symbol metadata, timestamps, candles/ticks, freshness and normalization.
- **Strategy Agents:** emit intents with symbol, side, type, requested quantity, stop-loss, take-profit, strategy ID and client order ID. Strategies do not submit orders directly.
- **Risk & Authorization:** deterministic per-trade risk, daily loss, drawdown, exposure, margin, leverage, symbol allowlists, stale-data, duplicate-intent and environment checks. LLM output cannot loosen limits.
- **Venue Agents:** Binance Futures adapter and MT5 bridge translate intents to broker-specific requests and venue states back.
- **OMS:** durable idempotent order lifecycle. Unknown submission state must never trigger blind resubmission.
- **Reconciliation:** read-only polling; update the ledger only on explicit, verifiable venue evidence.
- **Portfolio Agent:** read-only balances/positions; preserve native symbol, ticket, quantity, currency and venue. Never sum incompatible currencies or treat CFD lots as crypto quantity.
- **Observability/Audit:** health, structured events, orders/positions, risk decisions, latency, error state and tested kill switch.
- **FRIDAY integrations:** Inference, Futuris and IntelX are advisory-only; Memora is the authorized memory authority; Sentinel provides security/policy/audit integration. None independently authorizes or submits trades.

## Code observed on main
- stratex_agents/registry.py: agent catalog.
- stratex_agents/order_intent.py, order_store.py, order_manager.py, runtime.py: normalized intent and OMS foundations.
- stratex_agents/venue/binance_futures_oms_adapter.py: Binance Futures OMS and protective-order handling.
- exchanges/mt5_bridge_adapter.py, stratex_agents/venue/mt5_agent.py, mt5_bridge/server.py: MT5 bridge, venue agent and broker-aware risk preflight.
- stratex_agents/portfolio_agent.py: read-only multi-venue snapshot preserving native units/currencies.
- stratex_agents/reconciliation_agent.py and scripts/reconciliation_worker.py: evidence-based read-only reconciliation and opt-in worker.
- docs/STRATEX_AGENT_ARCHITECTURE.md: agent roles, MT5 risk preflight and worker defaults.
- Tests include MT5 bridge/adapter/venue agent, portfolio, reconciliation, Binance OMS and runtime factory. This handoff does **not** certify all current tests pass; rerun CI.

## MT5 risk controls observed
- POST /v1/risk/size uses MT5 broker calculations for stop-loss PnL and margin. Do not apply Binance quantity math to Forex/CFDs.
- Before an MT5 entry, the bridge rechecks account-currency stop-loss risk and margin, then calls MT5 order_check.
- Documented defaults: MT5_MAX_RISK_PER_TRADE=0.005 (0.5% of equity; code hard cap 2%) and MT5_MAX_MARGIN_FRACTION=0.20 (20% of equity, further constrained by free margin; code hard cap 35%). Verify current code and edge cases before relying on these.
- order_check is preflight, not a fill guarantee.
- Verify IC Markets demo mode explicitly. No credentials in GitHub or logs.

## Reconciliation invariants
- A missing order lookup is not proof of cancellation.
- Never blindly retry after a timeout or ambiguous send.
- A Binance entry is not fully protected unless both stop-loss and take-profit Algo Orders are confirmed working.
- MT5 reconciliation should use durable bridge records and retain ambiguous states for manual review.
- Worker is opt-in and must remain disabled until end-to-end Demo tests pass. Read current environment gates before enabling it.

## FRIDAY Universe infrastructure context (from user's master specification)
The spec describes nine services/repos: FRIDAY, Inference, Memora, Stratex, Forge, Cortex, Sentinel, IntelX and Futuris. Target: private Docker mesh friday_mesh; only reverse-proxy ports 80/443 public; specialists on internal port 8000 and FRIDAY on 10000; Memora is central memory authority; Sentinel/FRIDAY provide approval gates. This is a target specification, not proof every target is deployed.
The spec describes interim OCI AMD64 as about 1 OCPU, ~956 MB RAM, 8 GB swap and 50 GB disk; target OCI ARM64 A1.Flex is 2 OCPUs / 12 GB RAM / 150 GB disk. User says the nine agents currently run on the AMD 1 GB / 50 GB instance and Dokploy syncs GitHub repos to OCI. Do not assume the ARM host is allocated or target database migration is complete.
Nine services on 1 GB RAM is a serious constraint. Avoid heavyweight new services and concurrent builds. Measure memory, CPU, disk, swap and restarts. More disk does not solve RAM pressure. Prioritize bounded worker concurrency, resource limits, health checks and backups.

## Next execution plan

### Phase A — baseline
1. Fetch current main, inspect full history/diff and CI status.
2. Run lint, unit tests, dependency/security checks and credential-free integration tests.
3. Verify Binance Demo endpoint and Algo Order behavior against current official docs.
4. Verify MT5 bridge TLS/private networking, strong token, auth on every route, validation, rate limits, no public unauthenticated order endpoint, demo-only enforcement and safe logs.
5. Review state transitions, SQLite transaction boundaries, idempotency and worker restart/shutdown.

### Phase B — venue correctness
1. Binance Futures Demo: market/limit orders, partial fills, protective Algo Orders, cancellation, timeout, duplicate client ID, restart recovery and reconciliation.
2. MT5 Demo: symbol suffixes, order types, hedge-mode ticket identity, partial fills, stop distance, lot min/max/step, account-currency PnL, margin rejection, order_check, terminal disconnect and ambiguous states.
3. Prove Demo configuration cannot route to a live endpoint/account.
4. Align adapter contracts for health, symbols/metadata, balances, positions, submission, safe cancellation, order lookup and evidence-based reconciliation.

### Phase C — portfolio risk/accounting
1. Verify shared equity, daily realized/unrealized loss, max drawdown, exposure and venue-specific margin budgets.
2. Convert currencies only with explicit FX rates and timestamps; otherwise show native balances and mark total equity unavailable.
3. Keep append-only audit/event history and explicit corrections.
4. Implement stale-data circuit breakers and emergency halt with tests.

### Phase D — integration/deployment
1. Wire portfolio/reconciliation into a bounded supervised runtime, not an uncontrolled background thread.
2. Keep worker flags off by default.
3. Test Docker Compose/Dokploy environment, health checks, persistent volumes, migrations, resource limits and rollback.
4. Add tests/docs, use feature branches and PRs to main. Do not bypass review unless user explicitly asks for that specific change.
5. Deploy Demo only after CI passes; verify health/read-only endpoints, then test a small controlled demo order and protective-order lifecycle.
6. No live-money trading without separate explicit approval, signed authorization and verified risk gates.

## Secrets and access
Never ask the user to paste exchange secrets into chat, PR comments, source code or this PDF. Prefer a secrets manager or Dokploy environment secrets. If credentials must be entered, ask the user to enter them directly in the private UI/host; confirm configuration status, not secret values. Disable withdrawals/transfers, use Demo/test credentials, restrict IPs where supported and rotate exposed credentials.

## Unverified limitations
- Repository files do not prove an MT5 terminal/bridge is deployed or reachable from OCI.
- Do not assume MT5 desktop runs natively on OCI Linux. A separate Windows environment/VPS or validated bridge may be needed; confirm current platform/package requirements and budget.
- The 1 GB AMD host may be inadequate for nine services and trading. Measure before promising production reliability.
- A portfolio snapshot and callable reconciler are not fully wired scheduled monitoring.
- Per-trade controls do not replace portfolio-wide daily-loss/drawdown controls.
- No live-trading readiness claim until the full test/recovery matrix passes.

## Instructions for the next chat
Resume from current GitHub main and this handoff. First verify repo state and CI, then report a status matrix: implemented / partial / missing / tested. Continue through feature branches and PRs. Prioritize correctness and safety over adding agent names. Never request API secrets in chat. Do not begin by asking the user to run terminal commands; perform available repository review first and state only genuinely missing access or confirmation.
