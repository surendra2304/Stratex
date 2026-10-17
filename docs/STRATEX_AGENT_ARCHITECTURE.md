# Stratex Agent Architecture

## Design rule

Stratex is a single trading platform, not a collection of independent trading bots. Venue agents own broker-specific connectivity and execution semantics. Strategies emit normalized trade intents; deterministic policy/risk gates approve or reject them; order management persists and reconciles the lifecycle; portfolio and audit components observe resulting positions and fills.

No AI/LLM agent has direct order authority. Inference, Futuris and IntelX are advisory-only inputs. Sentinel/authorization policy and deterministic risk checks cannot be overridden by an LLM recommendation.

## Agent roles

| Agent | Responsibility | Order authority |
| --- | --- | --- |
| Stratex Core Orchestrator | Coordinates lifecycle and configuration | No independent authority |
| Market Data Agent | Normalizes tickers/candles/symbol metadata and freshness | None |
| Strategy Supervisor | Loads existing strategy registry; only validated strategies may propose executable signals | None |
| Risk & Authorization Agent | Deterministic account/portfolio limits, kill switch, order permission | May deny; cannot bypass policy |
| Binance Futures Venue Agent | Selects configured demo/testnet endpoints and owns Binance client credentials | Venue operations only after Core policy |
| IC Markets MT5 Venue Agent | Talks to a demo-only Windows bridge and preserves order/position tickets | Venue operations only after Core policy |
| Portfolio & Exposure Agent | Aggregates balances and exposures without treating one venue's rules as another's | None |
| Order Reconciliation Agent | Resolves intent/ack/fill/open-order/position state after timeouts/restarts | Must not blindly retry an ambiguous order |
| Inference Advisory Agent | Optional LLM/context summaries | None |
| Futuris Forecast Agent | Optional calibrated regime/forecast features | None |
| IntelX Research Agent | Optional source-backed event/news context | None |
| Audit & Monitoring Agent | Records mode, readiness, order lifecycle events, rejections and incidents | None |

The initial /api/agents endpoint returns a safe, credential-free catalog and configuration status. Configured does not mean the remote connection has passed a health check, and it does not mean the venue is authorized to execute orders.

## Binance endpoint modes

- BINANCE_DEMO_TRADING=true: uses new Binance Demo Trading credentials from BINANCE_API_KEY and BINANCE_API_SECRET, pins Spot API access to https://demo-api.binance.com/api and USDⓈ-M Futures REST access to https://demo-fapi.binance.com/fapi.
- BINANCE_DEMO_TRADING=false: preserves legacy Testnet behavior using API_KEY / SECRET_KEY and legacy testnet URLs.
- The two key sets are not interchangeable. When current Demo Trading mode is selected, the agent fails closed if new credentials are missing; it never falls back to legacy variables.
- LIVE_TRADING_ENABLED must remain false. The venue agent rejects live mode and rejects unrecognized endpoint URLs.

Binance REST endpoint references: [USDⓈ-M Futures general information](https://developers.binance.com/docs/derivatives/usds-margined-futures/general-info) and [CCXT Binance documentation](https://github.com/ccxt/ccxt/wiki/binance).

## MT5 endpoint modes

The bridge belongs to this repository but is deployed separately beside the MetaTrader 5 terminal, normally on a Windows VPS. It must bind to loopback or a private VPN address, authenticate every request, and refuse non-demo accounts. Do not expose its HTTP port publicly.

The MT5 bridge currently provides a transport/API foundation. Before connecting it to autonomous strategy execution, complete and validate: strategy-to-MT5 order lifecycle, broker-specific symbol/contract mapping, attached stop-loss and take-profit for market orders, current-order/position reconciliation, client-side idempotency across timeouts, accurate commission/swap accounting, retry rules, and end-to-end demo tests.

## Operational stages

1. Validate Binance Demo connection and runtime endpoint selection.
2. Make the existing order lifecycle venue-aware without forking strategy logic.
3. Finish a unified OMS with client IDs, persisted intents, fill-state tracking and reconciliation.
4. Integrate MT5 orders with broker-native attached protection and close-by-ticket semantics.
5. Add per-account risk limits and portfolio aggregation without cross-venue lot-size assumptions.
6. Run paper simulation and adapter contract tests; then a demo-only soak test with restart/disconnect/partial-fill cases.
7. Integrate FRIDAY, Inference, Futuris and IntelX as advisory inputs only. Never expose direct order permissions to an LLM.
8. Keep the live-money path blocked unless a future, separately reviewed design explicitly changes that invariant.

## Secure credential handling

Never commit or chat-post API keys, API secrets, MT5 passwords, bridge tokens or OCI credentials. Set them in the private deployment environment or secret manager. For Binance Demo Trading, use a newly generated Demo Trading key with the smallest permissions needed for demo account/order operations; withdrawals/transfers should remain disabled. MT5 credentials should be entered only into the MT5 terminal on its host.
