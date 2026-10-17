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

## Durable OMS foundation

The repository now includes `stratex_agents.order_intent.TradeIntent`, `stratex_agents.order_store.OrderIntentStore` and `stratex_agents.oms.StratexOrderManager`. The manager:

- requires `STRATEX_EXECUTION_MODE=DEMO` before any venue submission;
- rejects live-money mode and enforces a venue allowlist;
- applies an injected deterministic authorization/risk decision and the existing panic/kill-switch state;
- persists an intent before it crosses the venue boundary;
- reuses completed results for an identical client ID and rejects ID reuse with a different payload;
- marks ambiguous submit outcomes `UNKNOWN` and refuses blind retry until reconciliation resolves them;
- intentionally does not automatically fail over from one venue to another.

This is a durable OMS foundation with contract tests, not yet an assertion that every legacy strategy uses the manager. Wiring the existing signal/trailing/protection lifecycle through this manager is a separate integration milestone.


## Binance Futures OMS adapter foundation

`stratex_agents.venue.binance_futures_oms_adapter.BinanceFuturesOMSAdapter` implements the venue submitter contract for market-entry intents in the selected Binance Demo Trading or legacy Futures Testnet environment. It verifies the account position mode, loads the venue's symbol filters and quantity step, checks the configured contract is tradable and the order is above minimum notional, submits with the stable client order ID, and establishes both broker-native stop-loss and take-profit conditional orders using the Futures Algo Order API.

- It is deliberately limited to MARKET entries for this phase; LIMIT and STOP entries are refused until their asynchronous fill and protection lifecycle is implemented.
- It respects One-way and Hedge position mode, using positionSide where required.
- If bracket creation fails after entry, it attempts to close the filled quantity; it cancels an acknowledged stop only after the close is confirmed. If safety close is ambiguous, the stop is preserved and the shared OMS records UNKNOWN.
- The adapter doesn't decide whether a strategy signal should trade. Call it only through the shared OMS after deterministic risk and authorization checks.
- Conditional Futures orders use `futures_create_algo_order`; conditional types moved to the algo API in recent Binance API changes. See [python-binance Futures Algo Order example](https://github.com/sammchardy/python-binance/blob/master/examples/futures_algo_order_examples.py) and [Binance USDⓈ-M Futures order documentation](https://developers.binance.com/docs/derivatives/usds-margined-futures/trade/rest-api/New-Algo-Order).

This does not yet migrate every legacy `bot.py` entry, trailing stop, close and ledger path through the OMS. Do not treat the adapter foundation as production approval.


## Venue-neutral market-data agent

`stratex_agents.market_data.MarketDataAgent` normalizes instrument metadata, quotes and OHLCV candles from the explicitly selected venue. It preserves exact broker-native symbols (including IC Markets suffixes such as `XAUUSD.a`) and distinguishes Binance crypto perpetuals from MT5 Forex, commodity, index, equity and other CFD classifications based on broker metadata. It does not fabricate missing prices and never falls back to another venue's market data.

The MT5 bridge's authenticated `GET /v1/symbols` endpoint returns broker-native names and contract details such as contract size, profit/margin currencies, digits, tick size/value, minimum/maximum/step volume, stop distance, swap and margin fields. Filter by query or Market Watch visibility when the terminal's full symbol catalogue is large. Market-data endpoints do not submit orders.

Dashboard read-only endpoints (API read scope required):
- `GET /api/v1/market/instruments?venue=all`
- `GET /api/v1/market/quote?venue=mt5&symbol=EURUSD`
- `GET /api/v1/market/candles?venue=mt5&symbol=EURUSD&timeframe=15m&limit=300`

The API returns normalized market values but does not imply a signal is approved or an execution venue is enabled. Current MT5 availability still depends on a configured, authenticated, demo-only Windows bridge.
