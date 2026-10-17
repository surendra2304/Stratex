# Stratex: Binance Futures Demo + IC Markets MT5 Demo Rollout

## Architecture decision

Keep strategies, normalized signals, risk checks, authorization, portfolio accounting, the order ledger, reconciliation and monitoring in Stratex Core. Venue-specific code belongs in adapters. Binance and MT5 are execution venues, not independent strategy bots.

The MT5 adapter/bridge is packaged in this repository but deployed separately: the bridge must run on Windows beside the MT5 terminal, while Stratex Core may remain on OCI. This gives deployment isolation without duplicating strategy logic or maintaining a second repository.

## Implementation sequence

1. **Freeze the live-money boundary.** Keep `LIVE_TRADING_ENABLED=False`; add a venue allowlist and explicit environment mode per adapter. Default all new adapters disabled.
2. **Binance USDⓈ-M Futures Demo.** Verify the API key was created for the Futures Demo environment. Use the dedicated Demo REST endpoint `https://demo-fapi.binance.com`; do not reuse production URLs or assume Spot Testnet credentials are interchangeable. Validate account, exchange metadata, precision, minimum notional, hedge/one-way mode, leverage and margin configuration before order submission.
3. **MT5 demo bridge.** Run a Windows bridge next to the IC Markets MT5 terminal. Require a private network and bearer token, verify the account server is demo, normalize broker symbols/contract specs, and preserve position/order/deal tickets so MT5 hedge-mode positions are not collapsed.
4. **One order lifecycle.** Give every internal order a stable client ID and persist intent before submission. On timeout, query/reconcile before retrying; never blindly resubmit an ambiguous order. Record submit, acknowledgement, partial fill, fill, cancel, reject and reconciliation events.
5. **Unified risk, separate account limits.** Apply strategy-level limits plus per-venue limits for equity, exposure, margin, daily loss, drawdown, symbol permissions, minimum/step volume, max positions and stale market data. Never assume crypto quantity maps directly to MT5 lots.
6. **Health and kill switch.** Stop new orders if account/market data is stale, bridge health fails, reconciliation differs beyond tolerance, loss limits trip, or authorization is absent. Emergency flattening must be a separately authenticated, explicit operation and should not be triggered by a mere health-check failure.
7. **Paper and demo validation.** Run deterministic adapter contract tests, mocked failure tests, then demo-only end-to-end tests. Validate restart recovery, duplicate prevention, partial fills, disconnects, market closure, rejected volume, and hedge-mode close-by-ticket behavior.
8. **FRIDAY integration.** Keep Inference/Futuris advisory-only. Sentinel can authorize or deny an order, but no LLM response should directly place orders or override deterministic risk gates.
9. **Promotion criteria.** Do not enable a venue for autonomous demo execution until adapter tests, reconciliation, safety gates and readiness checks pass. No live-money mode is included in this rollout.

## Deployment topology

- OCI/Dokploy: Stratex Core, risk/order orchestration, database/ledger and monitoring.
- Windows VPS: MT5 desktop terminal + MT5 bridge, with outbound/private-VPN connectivity to Stratex.
- Binance: Stratex Core calls the Futures Demo API over HTTPS.
- Secrets: deployment environment/secret manager only; never in source, logs, or screenshots.

## Exit criteria

- Both adapters report account mode and capabilities accurately.
- No fallback/fabricated prices or balances on API failures.
- Idempotency and reconciliation tests pass.
- MT5 positions retain tickets and support hedge mode.
- A disabled or unhealthy adapter cannot receive an order.
- Live trading remains disabled.
